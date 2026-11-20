"""Guard for the indexing path's "log a failure once" rule (docs/logging.md, "Indexing").

A record's failure is written once, on the consumer's ``index.end``. So in these files an ``except`` block
that logs at WARNING or above and then raises is allowed only where it is listed below (kept on purpose: a
second, different failure, a lifecycle error, a fallback that raises on its last attempt). The table is exact:
each block is named by its function and what it logs first, so a new block fails with its file and line even
where it replaces a listed one, and a removed one fails until its row is lowered: the list only shrinks. Log calls in these files carry no emoji.

Out of the set until their own PR (P6b): blob_storage.py, the parsers and docling_processor.
"""

import ast
import importlib.util
import types
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]  # backend/python
_LOG_METHODS = frozenset({"warning", "error", "exception", "critical"})
_LOUD_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL"})
_QUIET_LEVELS = frozenset({"DEBUG", "INFO"})

ALLOWED: dict[str, dict[str, int]] = {
    "app/events/processor.py": {"project_code_blocks_to_graph:Failed to project code blocks to graph f": 1},
    "app/events/events.py": {
        "_dispatch_pdf_binary:index.parse_fallback": 1,
        "update_record_fields:Could not register the records still sha": 1,
        "_skip_same_bytes:Record %s: the same-bytes skip could not": 1,
    },
    "app/services/messaging/kafka/handlers/record.py": {
        "_index_from_blob:index.blob_read_failed": 1,
        "process_event:index.status_write_failed": 2,
        "_download_from_signed_url:Download attempt {} failed with {}: {}.": 1,
    },
    "app/services/messaging/kafka/utils/utils.py": {},
    "app/modules/transformers/vectorstore.py": {"_ask_chunk:Embedding server refused %d texts as too": 1},
    "app/modules/transformers/sink_orchestrator.py": {"_mark_parse_now_stored:Parse-now marker of record %s not writte": 1},
    "app/services/messaging/redis_streams/indexing_consumer.py": {
        "initialize:Failed to create consumer: %s": 1,
        "start:Failed to start Redis Streams consumer:": 1,
        # raises only when nothing was read; logs only when it hands the rest on
        "__own_unheld_pending_payloads:Pending scan on %s stopped after %d entr": 1,
        # the ack's own failure, in the delivery's context (index.end keeps the record's)
        "_abandon_message:index.ack_failed": 1,
    },
    "app/services/messaging/kafka/consumer/indexing_consumer.py": {
        "initialize:Failed to create consumer: {}": 1,
        "start:Failed to start Kafka consumer: {}": 1,
        "__commit_if_appropriate:index.requeue_failed": 1,
        "__process_bound_message:index.requeue_failed": 1,
        "__settle_noted:index.commit_failed": 1,
    },
    "app/utils/indexing_timing.py": {},
    "app/services/messaging/postgres/consumer.py": {},
    "app/modules/pipeline/worker.py": {"_handle_job:stage.run_failed": 1},
}


def _own_nodes(body: list[ast.stmt]) -> list[ast.AST]:
    """The nodes of an ``except`` body, without nested handlers or functions: those are checked on their own."""
    found: list[ast.AST] = []
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        found.append(node)
        pending.extend(
            child for child in ast.iter_child_nodes(node)
            if not isinstance(child, (ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        )
    return found


def _level_name(level: ast.AST | None) -> str | None:
    """``logging.WARNING`` and an imported ``WARNING`` alike; None for anything else (a variable, a conditional)."""
    if isinstance(level, ast.Attribute):
        return level.attr
    if isinstance(level, ast.Name):
        return level.id
    return None


def _call_level(call: ast.Call) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == "level"), None)


def _loud_call(nodes: list[ast.AST]) -> ast.Call | None:
    """The first log call at WARNING or above among ``nodes``."""
    for call in sorted((n for n in nodes if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset)):
        if _is_loud(call):
            return call
    return None


def _is_loud(call: ast.Call) -> bool:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if isinstance(func, ast.Attribute) and name in _LOG_METHODS:
        return True
    if name == "log_failure":
        return True
    if name == "log" and isinstance(func, ast.Attribute):
        # logger.log(<level>, …) or logger.log(level=…): loud unless the level is DEBUG or INFO by name
        return _level_name(call.args[0] if call.args else _call_level(call)) not in _QUIET_LEVELS
    return name == "log_event" and _level_name(_call_level(call)) in _LOUD_LEVELS


def _what_it_logs(call: ast.Call) -> str:
    """The event name or message the call writes (its first string argument, ``{}`` for an f-string's values), cut short."""
    for arg in [*call.args, *(k.value for k in call.keywords if k.arg == "msg")]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value[:40].rstrip()
        if isinstance(arg, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in arg.values)[:40].rstrip()
    return "?"


def log_and_reraise_blocks(source: str) -> list[tuple[int, str]]:
    """(line, handler) of each ``except`` block that logs at WARNING+ and raises. The handler is named by its function and
    what it logs first (``process_event:'index.status_write_failed'``): stable across edits elsewhere in the file."""
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.ExceptHandler):
                own = _own_nodes(child.body)
                loud = _loud_call(own)
                if loud is not None and any(isinstance(n, ast.Raise) for n in own):
                    found.append((child.lineno, f"{function}:{_what_it_logs(loud)}"))
            visit(child, function)

    visit(ast.parse(source), "<module>")
    return found


def _log_emoji() -> types.ModuleType:
    path = ROOT.parents[1] / "scripts" / "log_emoji.py"  # imported by path, as the CI gate runs it
    spec = importlib.util.spec_from_file_location("log_emoji", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("relative", sorted(ALLOWED))
def test_failures_are_not_logged_and_reraised(relative: str) -> None:
    blocks = log_and_reraise_blocks((ROOT / relative).read_text(encoding="utf-8"))
    counts = Counter(handler for _, handler in blocks)
    allowed = ALLOWED[relative]
    over = [f"{relative}:{line} ({handler})" for line, handler in blocks if counts[handler] > allowed.get(handler, 0)]
    assert not over, "logs a failure it re-raises (the consumer's index.end writes it once): " + ", ".join(over)
    assert dict(counts) == allowed, f"{relative}: lower ALLOWED to {dict(counts)} (the list only shrinks)"


@pytest.mark.parametrize("relative", sorted(ALLOWED))
def test_no_emoji_in_log_calls(relative: str) -> None:
    rows = _log_emoji().findings((ROOT / relative).read_text(encoding="utf-8"))
    assert rows == [], f"{relative}: emoji in the log calls at lines {rows}"


def test_the_scanner_sees_log_failure_and_loud_log_event_but_not_a_quiet_one() -> None:
    source = '''
def a():
    try:
        x()
    except Exception as e:
        log_failure(logger, "x.failed", e)
        raise
def b():
    try:
        x()
    except Exception:
        log_event(logger, "x", level=logging.WARNING)
        raise
def c():
    try:
        x()
    except Exception:
        logger.debug("fine")
        raise
def d():
    try:
        x()
    except Exception as e:
        logger.error("swallowed: %s", e)
def e():
    try:
        x()
    except Exception:
        self.logger.log(logging.ERROR, "f")
        raise
def f():
    try:
        x()
    except Exception:
        self.logger.log(logging.DEBUG if quiet else logging.WARNING, "f")
        raise
def g():
    try:
        x()
    except Exception:
        self.logger.log(logging.INFO, "f")
        raise
def h():
    try:
        x()
    except Exception:
        logger.log(level=logging.WARNING, msg="h.failed")
        raise
def i():
    try:
        x()
    except Exception:
        logger.log(level=logging.INFO, msg="fine")
        raise
def j():
    try:
        x()
    except Exception:
        log_event(logger, "j.failed", level=WARNING)
        raise
def k():
    try:
        x()
    except Exception:
        try:
            y()
        except Exception as e:
            log_failure(logger, "k.inner_failed", e)
            raise
'''
    assert [handler for _, handler in log_and_reraise_blocks(source)] == [
        "a:x.failed", "b:x", "e:f", "f:f", "h:h.failed", "j:j.failed", "k:k.inner_failed",  # k's outer block is not counted again
    ]
