"""A graph provider method that re-raises a failure does not log it, and one that carries on or answers a default logs it once,
without its text (docs/logging.md, "Database queries").

The client already wrote the text-free ``db.query.failed`` record and stamped the exception ``hide_text``; the handler that
catches the exception upstream writes the one failure line. The provider's own ``❌ <method> failed: {str(e)}`` line was a
duplicate that also printed the driver's text, which can quote the query's bound values (an email, a record name, a path).
A step that fails and carries on writes ``graph.provider.step_failed``, and a method that answers its default (``None``, ``[]``,
``False``) writes ``graph.provider.failed``, through ``log_failure(..., include_error_text=False)``: no other log call sits in
a provider ``except`` handler, and none comes before a raise the handler can still reach.
"""
from __future__ import annotations

import ast
import contextvars
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import OrgContext, bind
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.provider import PostgresGraphProvider
from app.utils import error_class
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


def _driver_error(*, stamped: bool, error_class: str = "internal") -> Exception:
    exc = RuntimeError(f"MATCH (u:User {{email: '{SENTINEL}'}}) RETURN u")
    if stamped:  # as neo4j_client / arango_http_client / uow leave it after db.query.failed
        mark_error(exc, error_class, hide_text=True)
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
    # the agent-usage reads a model or web search provider delete is refused on: a failure raises, never reads as no agents
    *[(backend, method, ("org-1", SENTINEL), {}) for backend in ("neo4j", "arango", "postgres")
      for method in ("get_agents_by_model_key", "get_agents_by_web_search_provider")],
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


def log_failures_before_a_raise(source: str) -> list[int]:
    """Lines of ``log_failure`` calls inside an ``except`` handler from which a ``raise`` can still be reached (plan H3): the
    call marks the exception logged, so the handler that catches the raise would write its line at DEBUG. A ``return`` after
    the call (or ``return log_failure(...)`` itself) ends its path, except for the ``finally`` blocks it leaves, which still
    run; a ``break`` or ``continue`` resumes after its loop, or ends the path when the loop encloses the handler; a loop inside
    the handler that goes round again runs its whole body again (the back edge); a ``try``'s or loop's ``else`` runs when its
    body completes; a ``log_event`` (it marks nothing) may come before a raise. A lint, not a
    control-flow analyser: a raise the handler catches again itself still counts (strict: a false hit, never a miss)."""
    hits: list[int] = []
    for handler in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ExceptHandler)):
        hits += [line for line, path in _statement_paths(handler.body) if _is_log_failure(path[-1][0][path[-1][1]])
                 and _a_raise_follows(path)]
    return sorted(hits)


def _statement_paths(block: list[ast.stmt], outer: tuple = (), owner: ast.stmt | None = None) -> Iterator[tuple[int, tuple]]:
    """Every statement of the block and the blocks inside it (not a nested ``except`` handler's: that one is checked on its
    own), with its path: the (block, index, owner) triples from the handler's body down to it, ``owner`` being the statement
    whose ``body``, ``orelse`` or ``finalbody`` the block is (``None`` for the handler's body)."""
    for i, st in enumerate(block):
        path = (*outer, (block, i, owner))
        yield st.lineno, path
        for field in ("body", "orelse", "finalbody"):
            sub = getattr(st, field, None)
            if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt) and not isinstance(st, ast.FunctionDef | ast.AsyncFunctionDef):
                yield from _statement_paths(sub, path, st)
        for case in getattr(st, "cases", None) or []:  # each ``match`` case is its own branch
            yield from _statement_paths(case.body, path, st)


def _is_log_failure(st: ast.stmt) -> bool:
    """``log_failure(...)`` as a statement, or ``return log_failure(...)``."""
    return isinstance(st, ast.Expr | ast.Return) and isinstance(st.value, ast.Call) and _name_of(st.value) == "log_failure"


def _raises(st: ast.stmt) -> bool:
    return any(isinstance(n, ast.Raise) for n in ast.walk(st))


_LOOPS = ast.For | ast.AsyncFor | ast.While


def _a_raise_follows(path: tuple) -> bool:
    # "flow": the statements after the call run; "continue" / "break": the innermost loop is re-entered or left (falling off
    # its body is a "continue"); "return": only the finally blocks on the way out run
    block, i, _ = path[-1]
    mode = "return" if isinstance(block[i], ast.Return) else "flow"
    for block, i, owner in reversed(path):
        if mode == "flow":
            for st in block[i + 1:]:
                if _raises(st):
                    return True
                if isinstance(st, ast.Return | ast.Continue | ast.Break):
                    mode = {ast.Return: "return", ast.Continue: "continue", ast.Break: "break"}[type(st)]
                    break
            else:  # the block completed: a try's else runs next, and a loop goes round again
                if isinstance(owner, ast.Try) and block is owner.body and any(_raises(st) for st in owner.orelse):
                    return True
                if isinstance(owner, _LOOPS) and block is owner.body:
                    mode = "continue"
        if mode in ("continue", "break") and isinstance(owner, _LOOPS) and block is owner.body:
            # the next iteration runs the whole body again, then the loop's else; a break skips both
            if mode == "continue" and any(_raises(st) for st in (*owner.body, *owner.orelse)):
                return True
            mode = "flow"  # the statements after that loop run
        if isinstance(owner, ast.Try) and block is not owner.finalbody and any(_raises(st) for st in owner.finalbody):
            return True
    return False


def _name_of(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


@pytest.mark.parametrize("path", PROVIDER_FILES, ids=lambda p: p.name)
def test_no_provider_handler_logs_a_failure_before_it_can_reraise_it(path: Path) -> None:
    hits = log_failures_before_a_raise(path.read_text())
    assert not hits, f"{path.name}: log_failure before a reachable raise, lines {hits[:10]}"


_FAILED = 'error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="f")'


@pytest.mark.parametrize("handler", [
    f"except Exception as e:\n        {_FAILED}\n        raise",
    f'except Exception as e:\n        {_FAILED}\n        raise GraphQueryError("x") from e',
    f"except Exception as e:\n        {_FAILED}\n        if raise_on_error:\n            raise\n        return None",
    f"except Exception as e:\n        {_FAILED}\n        if transaction:\n            raise\n        return False",
    f"except Exception as e:\n        if retry:\n            {_FAILED}\n        raise",
    # a retry loop's last lost round: its log comes right before the raise (PR 2 review, M14)
    f"except Exception as e:\n        if attempt < 3:\n            await asyncio.sleep(1)\n            continue\n        {_FAILED}\n        raise",
    # a finally that re-raises runs after the line, whether the try body falls through or returns (CodeRabbit, #6257)
    f"except Exception as e:\n        try:\n            {_FAILED}\n        finally:\n            raise",
    f"except Exception as e:\n        try:\n            {_FAILED}\n            return None\n        finally:\n            if strict:\n"
    "                raise",
    # #6257 review: a try's else after a body that completes; a break resumes after its loop; return log_failure(...) leaves
    # through a raising finally; a match case
    f"except Exception as e:\n        try:\n            {_FAILED}\n        except OSError:\n            pass\n        else:\n            raise",
    f"except Exception as e:\n        for key in keys:\n            {_FAILED}\n            break\n        raise",
    # a loop inside the handler goes round again: a raise earlier in its body runs after the line (#6257 review, back edge)
    f"except Exception as e:\n        for attempt in range(2):\n            if attempt:\n                raise\n            {_FAILED}\n"
    "        return None",
    f"except Exception as e:\n        for attempt in range(2):\n            if attempt:\n                raise\n            {_FAILED}\n"
    "            continue\n        return None",
    f"except Exception as e:\n        try:\n            return {_FAILED}\n        finally:\n            raise",
    f'except Exception as e:\n        match mode:\n            case "strict":\n                {_FAILED}\n                raise\n'
    "            case _:\n                return None",
])
def test_the_order_guard_finds_a_planted_log_before_a_reachable_raise(handler: str) -> None:
    source = f"async def f(self):\n    while True:\n        try:\n            await q()\n        {handler.replace(chr(10), chr(10) + '    ')}\n"
    hits = log_failures_before_a_raise(source)
    assert len(hits) == 1, source
    assert "log_failure" in source.splitlines()[hits[0] - 1]


@pytest.mark.parametrize("handler", [
    f"except Exception as e:\n        if raise_on_error:\n            raise\n        {_FAILED}\n        return None",
    f"except Exception as e:\n        if attempt < 3:\n            {_FAILED}\n            continue\n        raise",
    # PostgreSQL's recursive delete: its own transaction answers a failure dict, the caller's re-raises without a line
    f"except BaseException as e:\n        if transaction is None:\n            await rollback()\n            if isinstance(e, Exception):\n"
    f"                {_FAILED}\n                return {{}}\n        raise",
    'except Busy as busy:\n        log_event(self.logger, "kh.listing.out_of_memory", connector_id=app_id)\n        raise Refusal() from busy',
    "except Exception:\n        raise",
    # a finally that only tidies up, after a log that returns
    f"except Exception as e:\n        try:\n            {_FAILED}\n            return None\n        finally:\n            await release()",
    # the raise sits in the try body before the line, and the finally does not raise
    f"except Exception as e:\n        try:\n            if strict:\n                raise\n            {_FAILED}\n        finally:\n"
    "            await release()\n        return None",
    # their controls: a return skips the try's else; a break out of a loop followed by a return; return log_failure(...)
    # through a finally that only tidies up; a sibling match case is not a successor
    f"except Exception as e:\n        try:\n            {_FAILED}\n            return None\n        except OSError:\n            pass\n"
    "        else:\n            raise",
    f"except Exception as e:\n        for key in keys:\n            {_FAILED}\n            break\n        return None",
    # the back edge's controls: the line is followed by a return, or a break, so the body is not run again
    f"except Exception as e:\n        for attempt in range(2):\n            if attempt:\n                raise\n            {_FAILED}\n"
    "            return None",
    f"except Exception as e:\n        for attempt in range(2):\n            if attempt:\n                raise\n            {_FAILED}\n"
    "            break\n        return None",
    f"except Exception as e:\n        try:\n            return {_FAILED}\n        finally:\n            await release()",
    f'except Exception as e:\n        match mode:\n            case "lenient":\n                {_FAILED}\n                return None\n'
    "            case _:\n                raise",
])
def test_the_order_guard_leaves_a_log_on_a_path_that_ends_alone(handler: str) -> None:
    source = f"async def f(self):\n    while True:\n        try:\n            await q()\n        {handler.replace(chr(10), chr(10) + '    ')}\n"
    assert log_failures_before_a_raise(source) == []


# ---- every provider handler logs only through log_failure, text-free, under a provider event (plan M2) ----

PROVIDER_EVENTS = frozenset({"graph.provider.step_failed", "graph.provider.failed"})


def _mentions_the_exception(node: ast.AST, name: str | None | set[str | None]) -> bool:
    """Whether the node reads the caught exception (``name``, or any of a set of names derived from it) other than through
    ``type(<name>)`` or ``classify_error(<name>)``."""
    names = name if isinstance(name, set) else {name}
    read_safely = {
        id(n.args[0]) for n in ast.walk(node)
        if isinstance(n, ast.Call) and n.args and (
            (isinstance(n.func, ast.Name) and n.func.id == "type")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "classify_error"))
    }
    return any(isinstance(n, ast.Name) and n.id in names and id(n) not in read_safely for n in ast.walk(node))


def _carries_text(value: ast.AST, exc: str | None, derived: set[str]) -> bool:
    """Whether an assigned value can hold the exception's text: the exception itself, ``str``/``repr``/``format`` of it, an
    f-string or ``%``/``.format`` with it, its ``.args``; or any use of a local already derived that way. A number read off the
    exception (``busy.ran_s``) is not text."""
    def is_exc(n: ast.AST) -> bool:
        return isinstance(n, ast.Name) and n.id == exc
    for n in ast.walk(value):
        if isinstance(n, ast.Name) and n.id in derived:
            return True
        if isinstance(n, ast.FormattedValue) and any(is_exc(m) for m in ast.walk(n.value)):
            return True
        if isinstance(n, ast.Call) and (
                (isinstance(n.func, ast.Name) and n.func.id in {"str", "repr", "format"})
                or (isinstance(n.func, ast.Attribute) and n.func.attr == "format")) \
                and any(is_exc(m) for a in (*n.args, *(k.value for k in n.keywords)) for m in ast.walk(a)):
            return True
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod) and any(is_exc(m) for m in ast.walk(n.right)):
            return True
        if isinstance(n, ast.Attribute) and n.attr == "args" and is_exc(n.value):
            return True
    return is_exc(value)


def _exception_names(handler: ast.ExceptHandler) -> set[str | None]:
    """The handler's exception and every local assigned a value that can hold its text (``detail = str(e)``,
    ``msg = f"{e}"``, ``args = e.args``, ``err = e``), transitively."""
    derived: set[str] = set()
    assigns = [n for n in _own_nodes(handler) if isinstance(n, ast.Assign | ast.AnnAssign | ast.AugAssign | ast.NamedExpr)]
    grew = handler.name is not None
    while grew:
        grew = False
        for a in assigns:
            if a.value is not None and _carries_text(a.value, handler.name, derived):
                targets = a.targets if isinstance(a, ast.Assign) else [a.target]
                new = {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)} - derived
                derived |= new
                grew = grew or bool(new)
    return {handler.name, *derived}


def _a_default_follows(call: ast.Call, handler: ast.ExceptHandler) -> bool:
    """Whether the statement right after this call, in its block, returns a default (``None``, a constant, an empty
    ``[]``/``{}``/``set()``/``()``): the method's answer, so its line is ``graph.provider.failed``, not a step's. A bare
    ``return`` (a void method stopping) or a fallback's computed answer is not a default."""
    for _, path in _statement_paths(handler.body):
        block, i, _ = path[-1]
        if isinstance(block[i], ast.Expr) and block[i].value is call:
            nxt = block[i + 1] if i + 1 < len(block) else None
            value = nxt.value if isinstance(nxt, ast.Return) else None
            return isinstance(value, ast.Constant) or (
                isinstance(value, ast.List | ast.Tuple | ast.Set) and not value.elts) or (
                isinstance(value, ast.Dict) and not value.keys) or (
                isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in {"set", "list", "dict", "tuple"}
                and not value.args and not value.keywords)
    return False


def _bad_handler_log(call: ast.Call, handler: ast.ExceptHandler, method: str | None) -> bool:
    """Any logger call; a ``log_failure`` that may print the text (``include_error_text`` not ``False``, or the exception, or
    a local derived from it, in any field or ``**`` unpacking), names an event outside the provider's, logs an exception other
    than the one this handler caught, names an ``op`` other than ``method``, or writes ``step_failed`` right before returning
    a default; or a ``log_event`` that carries the exception (its text in a field)."""
    name = _name_of(call)
    names = _exception_names(handler)
    if name == "log_event":
        return _mentions_the_exception(call, names)
    if name == "log_failure":
        event, exc = (call.args[i] if len(call.args) > i else None for i in (1, 2))
        keywords = {k.arg: k.value for k in call.keywords if k.arg is not None}
        op = keywords.get("op")
        return not (
            isinstance(keywords.get("include_error_text"), ast.Constant) and keywords["include_error_text"].value is False
            and not any(_mentions_the_exception(k.value, names) for k in call.keywords)
            and isinstance(event, ast.Constant) and event.value in PROVIDER_EVENTS
            and isinstance(exc, ast.Name) and exc.id == handler.name
            and op is not None and (
                (isinstance(op, ast.Constant) and op.value == method)
                or (method is None and ast.unparse(op) == "fn.__name__"))  # a module-level wrapper of ``fn`` (``_soft``)
            and not (event.value == "graph.provider.step_failed" and _a_default_follows(call, handler))
        )
    return name in LEVELS and isinstance(call.func, ast.Attribute) and "log" in ast.unparse(call.func.value).lower()


def _methods(tree: ast.AST) -> dict[int, str | None]:
    """For each ``except`` handler (by id), the method its ``op`` names: the outermost function defined directly on a class
    that encloses it (a nested helper reports its method). ``None`` for a handler in a module-level wrapper that an enclosing
    function binds ``fn`` for (``_soft``): it names the method it wraps, ``fn.__name__``. Any other handler outside a class
    names its outermost function."""
    out: dict[int, str | None] = {}

    def visit(node: ast.AST, method: str | None, in_class: bool, in_method: bool, binds_fn: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ExceptHandler):
                out[id(child)] = None if binds_fn else method or ""
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                params = {a.arg for a in (*child.args.posonlyargs, *child.args.args, *child.args.kwonlyargs)}
                visit(child, child.name if in_class or method is None else method, in_class=False, in_method=in_method or in_class,
                      binds_fn=not (in_method or in_class) and (binds_fn or "fn" in params))
            else:
                visit(child, method, in_class=isinstance(child, ast.ClassDef), in_method=in_method, binds_fn=binds_fn)
    visit(tree, None, in_class=False, in_method=False, binds_fn=False)
    return out


def bad_logs_in_handlers(source: str) -> list[int]:
    """Lines of log calls inside any ``except`` handler that are not a text-free ``log_failure`` of the caught exception under
    a provider event with ``op`` = its method, or a ``log_event`` without the exception."""
    tree = ast.parse(source)
    methods = _methods(tree)
    hits: list[int] = []
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        hits += [n.lineno for n in _own_nodes(handler) if isinstance(n, ast.Call) and _bad_handler_log(n, handler, methods[id(handler)])]
    return sorted(hits)


@pytest.mark.parametrize("path", PROVIDER_FILES, ids=lambda p: p.name)
def test_no_provider_handler_logs_but_through_a_text_free_log_failure(path: Path) -> None:
    hits = bad_logs_in_handlers(path.read_text())
    assert not hits, f"{path.name}: log calls in except handlers that are not a text-free log_failure, lines {hits[:10]}"


_STEP = 'error_class.log_failure(self.logger, "graph.provider.step_failed", e, include_error_text=False, op="f", step="s")'


@pytest.mark.parametrize("handler", [
    'except Exception as e:\n        self.logger.warning(f"step failed: {e}")',
    'except Exception:\n        self.logger.warning("step failed for %s", key)',
    'except Exception:\n        self.logger.exception("step failed")',
    'except Exception as e:\n        msg = f"failed: {e}"\n        self.logger.error(msg)\n        failed += 1',
    'except Exception as e:\n        self.logger.warning("failed", extra={"err": str(e)})',
    'except Exception as e:\n        self.logger.debug("full-text unavailable, using scan", exc_info=True)',
    'except Exception:\n        self.logger.error(f"Traceback: {traceback.format_exc()}")\n        return None',
    'except Exception as e:\n        if raise_on_error:\n            raise\n        self.logger.error(f"batch failed: {e}")',
    'except Exception as e:\n        self.logger.error(f"failed: {str(e)}")\n        return None',
    'except Exception:\n        self.logger.error("failed for %s", key)\n        raise',
    'except Exception as e:\n        logging.getLogger(__name__).error("failed")\n        return []',
    'except Exception as e:\n        log = self.logger\n        log.error("failed")\n        return []',
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.provider.step_failed", e, op="f", step="s")',
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.f.failed", e, include_error_text=False)\n        return None',
    'except Exception as e:\n        error_class.log_failure(self.logger, f"graph.{op}.failed", e, include_error_text=False)\n        return None',
    'except Exception as rb_err:\n        error_class.log_failure(self.logger, "graph.provider.step_failed", e, include_error_text=False)',
    'except Exception as e:\n        log_event(self.logger, "graph.provider.step_failed", op="f", error=str(e))',
    'except Exception as e:\n        log_event(self.logger, "graph.provider.step_failed", op="f", cause=e)',
    # the text in a field of a text-free line (review MED-1), and an op that is not the method (LOW-1)
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="f",'
    ' detail=str(e))\n        return None',
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="f",'
    ' cause=repr(e.__cause__))\n        return None',
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="g")\n'
    '        return None',
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False)\n'
    '        return None',
    # #6257 review: the text in an earlier ``**`` unpacking, or in a local derived from the exception; a step line right
    # before the method's default answer
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="f",'
    ' **{"step": str(e)}, **{"collection": "records"})\n        return None',
    *(f'except Exception as e:\n        detail = {value}\n        error_class.log_failure(self.logger, "graph.provider.failed", e,'
      ' include_error_text=False, op="f", detail=detail)\n        return None'
      for value in ("str(e)", 'f"{e}"', "repr(e)", "e.args", '"failed: %s" % e', "e")),
    'except Exception as e:\n        msg = str(e)\n        detail = msg[:80]\n        error_class.log_failure(self.logger, "graph.provider.failed", e,'
    ' include_error_text=False, op="f", detail=detail)\n        return None',
    f"except Exception as e:\n        {_STEP}\n        return None",
    f"except Exception as e:\n        {_STEP}\n        return []",
])
def test_the_guard_finds_a_planted_log_in_a_handler(handler: str) -> None:
    source = f"async def f(self):\n    for key in keys:\n        try:\n            await q()\n        {handler.replace(chr(10), chr(10) + '    ')}\n"
    hits = bad_logs_in_handlers(source)
    assert len(hits) == 1, source
    assert "log" in source.splitlines()[hits[0] - 1]


@pytest.mark.parametrize("handler", [
    f"except Exception as e:\n        {_STEP}",
    f"except Exception as e:\n        if raise_on_error:\n            raise\n        {_STEP}\n        failed += 1",
    f"except Exception as e:\n        if raise_on_error:\n            raise\n        {_FAILED}\n        return None",
    'except Exception as e:\n        log_event(self.logger, "graph.provider.duplicates_removed", level=logging.WARNING, removed=1)',
    'except Exception as e:\n        log_event(self.logger, "graph.provider.step_failed", error_class=error_class.classify_error(e),'
    ' error_type=type(e).__name__)',
    'except Exception:\n        pass',
    'except Exception:\n        raise',
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="f",'
    ' error_class=error_class.classify_error(e))\n        return None',
    # their controls: a constant ``**`` unpacking; locals that hold no text (a type name, a number read off the exception);
    # a step that answers its fallback's result, or stops a void method
    'except Exception as e:\n        error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="f",'
    ' **{"collection": "records"})\n        return None',
    'except Exception as e:\n        kind = type(e).__name__\n        elapsed = round(e.ran_s * 1000)\n'
    '        error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="f", kind=kind,'
    ' duration_ms=elapsed)\n        return None',
    f"except Exception as e:\n        {_STEP}\n        return await self._fallback()",
    f"except Exception as e:\n        {_STEP}\n        return",
])
def test_the_guard_leaves_text_free_handlers_alone(handler: str) -> None:
    assert bad_logs_in_handlers(f"async def f(self):\n    try:\n        await q()\n    {handler}\n") == []


def test_the_op_of_a_nested_helper_or_a_wrapper_is_the_method_it_serves() -> None:
    nested = (
        "class P:\n    async def get_agents(self):\n        async def _enrich(a):\n            try:\n                await q()\n"
        "            except Exception as e:\n"
        '                error_class.log_failure(self.logger, "graph.provider.step_failed", e, include_error_text=False,'
        ' op="{op}", step="s")\n'
    )
    wrapper = (
        "def _soft(default):\n    def deco(fn):\n        async def wrapper(self):\n            try:\n                return await fn(self)\n"
        "            except Exception as e:\n"
        '                error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op=fn.__name__)\n'
        "                return default\n        return wrapper\n    return deco\n"
    )
    assert bad_logs_in_handlers(nested.format(op="get_agents")) == []
    assert bad_logs_in_handlers(wrapper) == []
    assert bad_logs_in_handlers(nested.format(op="_enrich")) == [7]
    # ``fn.__name__`` names the wrapped method only in a module-level wrapper; a method's callback named ``fn`` is not its op
    method_with_callback = (
        "class P:\n    async def get_document(self, fn):\n        try:\n            return await fn()\n        except Exception as e:\n"
        '            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op=fn.__name__)\n'
        "            return None\n"
    )
    assert bad_logs_in_handlers(method_with_callback) == [6]


def _provider_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER]


def _assert_text_free(caplog: pytest.LogCaptureFixture) -> None:
    for r in _provider_records(caplog):
        assert SENTINEL not in r.getMessage()
        assert SENTINEL not in str(r.exc_text or "")
        assert SENTINEL not in str(r.__dict__)
        assert getattr(r, "error_message", None) is None


def _the_one(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    records = [r for r in _provider_records(caplog) if getattr(r, "event_name", None) == event]
    assert len(records) == 1, [r.getMessage() for r in _provider_records(caplog)]
    return records[0]


FAILURES = [
    pytest.param(lambda: _driver_error(stamped=True), logging.ERROR, "internal", id="stamped-internal"),
    pytest.param(lambda: _driver_error(stamped=True, error_class="timeout"), logging.WARNING, "timeout", id="stamped-timeout"),
    pytest.param(lambda: _driver_error(stamped=False), logging.ERROR, "internal", id="unstamped"),
    pytest.param(lambda: KeyError(SENTINEL), logging.ERROR, "internal", id="non-db-keyerror"),
]


def _assert_level_and_stack(record: logging.LogRecord, level: int, cls: str) -> None:
    assert (record.levelno, record.error_class, record.outcome) == (level, cls, "failure")
    if level >= logging.ERROR:  # the stack once, every exception shown as ``Type: <message omitted>``
        assert record.exc_info is None
        assert "<message omitted>" in record.exc_text
    else:
        assert not record.exc_text


@pytest.mark.parametrize(("make", "level", "cls"), FAILURES)
@pytest.mark.parametrize("backend", ["neo4j", "arango", "postgres"])
async def test_a_swallowed_status_update_logs_once_without_the_text(caplog, backend, make, level, cls) -> None:
    exc = make()
    provider = _provider(backend, exc)
    provider.validator = MagicMock()  # Neo4j validates the status before the query

    assert await provider.update_indexing_status_for_record_ids(["r1"], "QUEUED") is None

    record = _the_one(caplog, "graph.provider.failed")
    assert record.op == "update_indexing_status_for_record_ids"
    assert record.error_type == type(exc).__name__
    _assert_level_and_stack(record, level, cls)
    _assert_text_free(caplog)


async def _a_failing_step(backend: str, exc: Exception) -> tuple[object, str, str]:
    """Runs one best-effort step of each store against a failing database: what the method answered, and the op and step."""
    provider = _provider(backend, exc)
    if backend == "postgres":
        provider._create_deleted_record_event_payload = AsyncMock(side_effect=exc)
        return await provider._delete_events([({"virtualRecordId": "v1"}, None)], {}), "_delete_events", "deletion_event_payload"
    return await provider._delete_nodes_by_keys(None, ["k1"], "records"), "_delete_nodes_by_keys", "batch_delete"


@pytest.mark.parametrize(("make", "level", "cls"), FAILURES)
@pytest.mark.parametrize("backend", ["neo4j", "arango", "postgres"])
async def test_a_failed_step_goes_on_and_logs_once_without_the_text(caplog, backend, make, level, cls) -> None:
    answer, op, step = await _a_failing_step(backend, make())

    assert answer == (None if backend == "postgres" else (0, 1))  # nothing deleted, one batch failed: the method went on
    record = _the_one(caplog, "graph.provider.step_failed")
    assert (record.op, record.step) == (op, step)
    if backend != "postgres":
        assert record.collection == "records"
    _assert_level_and_stack(record, level, cls)
    _assert_text_free(caplog)


@pytest.mark.parametrize(("method", "args"), [
    ("_delete_nodes_by_keys", (None, ["k1"], "records")),
    ("_delete_edges_by_node_ids", (None, ["records/k1"], ["permission"])),
])
async def test_a_batch_delete_that_re_raises_writes_no_step_line(caplog, method, args) -> None:
    """ArangoDB's ``raise_on_error`` paths re-raise before the step line: the caller's handler logs it once (plan H3)."""
    exc = _driver_error(stamped=True)
    with pytest.raises(RuntimeError) as raised:
        await getattr(_provider("arango", exc), method)(*args, raise_on_error=True)
    assert raised.value is exc
    assert not [r.getMessage() for r in _provider_records(caplog) if r.levelno >= logging.INFO]
    assert error_class.logged_level(exc) is None  # not marked logged: the handler that catches it keeps its level


def test_a_per_org_sweep_failure_is_left_to_the_pass_to_log(caplog) -> None:
    """The code-graph sweep raises its first organisation's failure after the loop: its per-org line is a stackless WARNING
    that does not mark the exception logged, so the handler of the raise writes the one stack at its own level."""
    provider = _provider("postgres", RuntimeError())
    exc, errors = _driver_error(stamped=True), []

    def one_org_fails() -> None:
        with provider._sweep("org-2", errors):
            raise exc

    contextvars.Context().run(one_org_fails)  # a sweep binds each org itself, outside any org of the test's

    record = _the_one(caplog, "graph.provider.step_failed")
    assert (record.levelno, record.op, record.step, record.target_org_id) == (logging.WARNING, "_sweep", "org_sweep", "org-2")
    assert (record.error_class, record.error_type, record.exc_text) == ("internal", "RuntimeError", None)
    assert errors == [("org-2", exc)]
    assert error_class.logged_level(exc) is None
    _assert_text_free(caplog)


# ---- PR 3: a handler that answers a default (None, [], False, a failure dict) logs once, text-free ----

READS = [
    pytest.param("get_document", ("k1", "records"), None, id="get_document"),
    pytest.param("get_records_by_virtual_record_id", ("v1",), [], id="get_records_by_virtual_record_id"),
]


@pytest.mark.parametrize(("make", "level", "cls"), FAILURES)
@pytest.mark.parametrize(("method", "args", "default"), READS)
@pytest.mark.parametrize("backend", ["neo4j", "arango", "postgres"])
async def test_a_swallowed_read_answers_its_default_and_logs_once_without_the_text(
    caplog, backend, method, args, default, make, level, cls,
) -> None:
    exc = make()

    assert await getattr(_provider(backend, exc), method)(*args) == default

    record = _the_one(caplog, "graph.provider.failed")
    assert (record.op, record.error_type) == (method, type(exc).__name__)
    _assert_level_and_stack(record, level, cls)
    _assert_text_free(caplog)


@pytest.mark.parametrize(("method", "args", "default"), READS)
@pytest.mark.parametrize("backend", ["neo4j", "arango", "postgres"])
async def test_a_read_asked_to_raise_writes_no_line_and_leaves_the_failure_unmarked(caplog, backend, method, args, default) -> None:
    """``raise_on_error`` is checked before the line (plan H3): the caller's handler logs the failure once, at its level."""
    exc = _driver_error(stamped=True)
    with pytest.raises(RuntimeError) as raised:
        await getattr(_provider(backend, exc), method)(*args, raise_on_error=True)
    assert raised.value is exc
    assert not [r.getMessage() for r in _provider_records(caplog) if r.levelno >= logging.INFO]
    assert error_class.logged_level(exc) is None


ARANGO_TRANSACTIONAL = [
    pytest.param("delete_records_and_relations", ("k1",), False, id="delete_records_and_relations"),
    pytest.param("delete_parent_child_edge_to_record", ("r1",), 0, id="delete_parent_child_edge_to_record"),
]


@pytest.mark.parametrize(("method", "args", "default"), ARANGO_TRANSACTIONAL)
async def test_an_arango_delete_in_the_callers_transaction_re_raises_without_a_line(caplog, method, args, default) -> None:
    exc = _driver_error(stamped=True)
    with pytest.raises(RuntimeError) as raised:
        await getattr(_provider("arango", exc), method)(*args, transaction="txn-1")
    assert raised.value is exc
    assert not [r.getMessage() for r in _provider_records(caplog) if r.levelno >= logging.INFO]
    assert error_class.logged_level(exc) is None


@pytest.mark.parametrize(("method", "args", "default"), ARANGO_TRANSACTIONAL)
async def test_an_arango_delete_on_its_own_answers_its_default_and_logs_once(caplog, method, args, default) -> None:
    assert await getattr(_provider("arango", _driver_error(stamped=True)), method)(*args) == default
    assert _the_one(caplog, "graph.provider.failed").op == method
    _assert_text_free(caplog)


PG_DELETES = [
    pytest.param("delete_records_recursive", (["r1"], "c1"), id="delete_records_recursive"),
    pytest.param("delete_single_record", ("r1",), id="delete_single_record"),
]


def _pg_delete(exc: Exception) -> PostgresGraphProvider:
    provider = _provider("postgres", exc)
    provider._inventory = AsyncMock(side_effect=exc)
    provider.begin_transaction = AsyncMock(return_value="own-txn")
    provider.rollback_transaction = AsyncMock()
    return provider


@pytest.mark.parametrize(("method", "args"), PG_DELETES)
async def test_a_postgres_delete_in_the_callers_transaction_re_raises_without_a_line(caplog, method, args) -> None:
    exc = _driver_error(stamped=True)
    provider = _pg_delete(exc)
    with pytest.raises(RuntimeError) as raised:
        await getattr(provider, method)(*args, transaction="txn-1")
    assert raised.value is exc
    provider.rollback_transaction.assert_not_awaited()  # the caller owns it
    assert not [r.getMessage() for r in _provider_records(caplog) if r.levelno >= logging.INFO]
    assert error_class.logged_level(exc) is None


@pytest.mark.parametrize(("method", "args"), PG_DELETES)
async def test_a_postgres_delete_in_its_own_transaction_rolls_back_and_logs_once(caplog, method, args) -> None:
    provider = _pg_delete(_driver_error(stamped=True))
    answer = await getattr(provider, method)(*args)
    assert (answer["success"], answer["code"]) == (False, 500)
    provider.rollback_transaction.assert_awaited_once_with("own-txn")
    assert _the_one(caplog, "graph.provider.failed").op == method
    _assert_text_free(caplog)
