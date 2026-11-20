"""A graph provider method that re-raises a failure does not log it (docs/logging.md, "Database queries").

The client already wrote the text-free ``db.query.failed`` record and stamped the exception ``hide_text``; the handler that
catches the exception upstream writes the one failure line. The provider's own ``❌ <method> failed: {str(e)}`` line was a
duplicate that also printed the driver's text, which can quote the query's bound values (an email, a record name, a path).
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import OrgContext, bind
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.provider import PostgresGraphProvider
from app.utils.error_class import mark_error

if TYPE_CHECKING:
    from collections.abc import Iterator

SENTINEL = "f24d-sentinel.alice@example.com"
LOGGER = "test.graph_provider_error_text"
GRAPH_DB = Path(__file__).parents[4] / "app/services/graph_db"
PROVIDER_FILES = [
    GRAPH_DB / "neo4j/neo4j_provider.py",
    GRAPH_DB / "arango/arango_http_provider.py",
    *sorted((GRAPH_DB / "postgres").glob("*.py")),
]
LEVELS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})


def _driver_error(*, stamped: bool) -> Exception:
    exc = RuntimeError(f"MATCH (u:User {{email: '{SENTINEL}'}}) RETURN u")
    if stamped:  # as neo4j_client / arango_http_client / uow leave it after db.query.failed
        mark_error(exc, "internal", hide_text=True)
    return exc


class _FailingClient:
    """Every client call raises the same driver error."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> AsyncMock:
        return AsyncMock(side_effect=self._exc)


def _provider(backend: str, exc: Exception) -> Neo4jProvider | ArangoHTTPProvider | PostgresGraphProvider:
    logger = logging.getLogger(LOGGER)
    if backend == "neo4j":
        p = Neo4jProvider(logger, MagicMock())
        p.client = _FailingClient(exc)
    elif backend == "arango":
        p = ArangoHTTPProvider(logger, MagicMock())
        p.http_client = _FailingClient(exc)
    else:
        p = PostgresGraphProvider.__new__(PostgresGraphProvider)
        p.logger = logger
        p._lrows = AsyncMock(side_effect=exc)  # every statement of these methods runs through these two
        p._q = AsyncMock(side_effect=exc)
        p.batch_upsert_nodes = AsyncMock(side_effect=exc)
    return p


@pytest.fixture(autouse=True)
def _capture(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    with bind(OrgContext("org-1", None, "job", "job:test", "t")):
        yield


STAMPS = [pytest.param(True, id="stamped"), pytest.param(False, id="unstamped")]
CASES = [
    ("neo4j", "delete_nodes", (["k1"], "records"), {}),
    ("arango", "delete_nodes", (["k1"], "records"), {}),
    ("neo4j", "get_records_by_file_path", ("c1", "/a/b.txt"), {}),
    ("arango", "get_records_by_file_path", ("c1", "/a/b.txt"), {}),
    ("postgres", "get_records_by_file_path", ("c1", "/a/b.txt"), {}),
    ("postgres", "batch_upsert_people", ([MagicMock()],), {}),
    ("postgres", "migrate_person_to_user", (SENTINEL, "u1", "org-1"), {}),
]


@pytest.mark.parametrize("stamped", STAMPS)
@pytest.mark.parametrize(("backend", "method", "args", "kwargs"), CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES])
async def test_a_reraised_failure_is_not_logged_by_the_provider(caplog, backend, method, args, kwargs, stamped) -> None:
    exc = _driver_error(stamped=stamped)
    with pytest.raises(Exception) as raised:
        await getattr(_provider(backend, exc), method)(*args, **kwargs)
    assert raised.value is exc or raised.value.__cause__ is exc  # the same failure reaches the caller
    records = [r for r in caplog.records if r.name == LOGGER and r.levelno >= logging.INFO]
    assert not records, [r.getMessage() for r in records]
    assert not [r for r in caplog.records if SENTINEL in r.getMessage() or SENTINEL in str(r.exc_text or "")]


def _own_nodes(handler: ast.ExceptHandler) -> Iterator[ast.AST]:
    """Every node of the handler's body, not entering a nested ``except`` handler (that one is checked on its own)."""
    stack: list[ast.AST] = list(handler.body)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, ast.ExceptHandler))


def _is_log_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "log_failure" or (func.attr in LEVELS and "logger" in ast.unparse(func.value))
    return isinstance(func, ast.Name) and func.id == "log_failure"


def log_calls_before_reraise(source: str) -> list[int]:
    """Lines of logger and ``log_failure`` calls inside an ``except`` handler that ends in a ``raise`` and has no ``return``,
    ``continue`` or ``break``. A ``log_event`` (an event with its own fields, never the exception) is allowed there."""
    hits: list[int] = []
    for handler in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ExceptHandler)):
        nodes = list(_own_nodes(handler))
        if not isinstance(handler.body[-1], ast.Raise) or any(isinstance(n, (ast.Return, ast.Continue, ast.Break)) for n in nodes):
            continue
        hits += [n.lineno for n in nodes if _is_log_call(n)]
    return sorted(hits)


@pytest.mark.parametrize("path", PROVIDER_FILES, ids=lambda p: p.name)
def test_no_provider_handler_logs_a_failure_it_reraises(path: Path) -> None:
    hits = log_calls_before_reraise(path.read_text())
    assert not hits, f"{path.name}: log calls in handlers that re-raise, lines {hits[:10]}"


@pytest.mark.parametrize("handler", [
    'except Exception as e:\n        self.logger.error(f"failed: {str(e)}")\n        raise',
    'except Exception:\n        self.logger.error("failed for %s", key)\n        raise',
    'except Exception as e:\n        self.logger.exception("failed")\n        raise GraphQueryError("x") from e',
    'except Exception as e:\n        msg = f"failed: {e}"\n        self.logger.error(msg)\n        raise',
    'except Exception as e:\n        self.logger.error("failed", extra={"err": str(e)})\n        raise',
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.x.failed", e, include_error_text=False)\n        raise',
    'except Exception as e:\n        if txn:\n            await rollback(txn)\n            self.logger.warning("rolled back")\n        raise',
])
def test_the_guard_finds_a_planted_log_before_a_reraise(handler: str) -> None:
    source = f"async def f(self):\n    try:\n        await q()\n    {handler}\n"
    hits = log_calls_before_reraise(source)
    assert len(hits) == 1
    assert "log" in source.splitlines()[hits[0] - 1]


@pytest.mark.parametrize("handler", [
    # a handling layer: it answers a default on some path, so its line is the one failure line
    'except Exception as e:\n        self.logger.error("failed")\n        if raise_on_error:\n            raise\n        return None',
    'except Exception as e:\n        if not retry:\n            self.logger.error("failed")\n            return None\n        raise',
    # a rollback failure is its own exception, logged in its own handler
    'except Exception as e:\n        try:\n            await rollback()\n        except Exception:\n            self.logger.warning("rollback failed")\n        raise',
    'except Exception:\n        raise',
    'except Busy as busy:\n        log_event(self.logger, "kh.listing.out_of_memory", connector_id=app_id)\n        raise Refusal() from busy',
])
def test_the_guard_leaves_handling_layers_alone(handler: str) -> None:
    assert log_calls_before_reraise(f"async def f(self):\n    try:\n        await q()\n    {handler}\n") == []
