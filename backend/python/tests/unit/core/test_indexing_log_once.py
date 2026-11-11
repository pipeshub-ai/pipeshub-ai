"""Guard for the indexing path's "log a failure once" rule (docs/logging.md, "Indexing").

A record's failure is written once, on the consumer's ``index.end``. So in these files an ``except`` block
that logs at WARNING or above and then raises is allowed only where it is listed below (kept on purpose: a
second, different failure, a lifecycle error, a fallback that raises on its last attempt). The table is exact:
a new block fails with its file, line and function, and a removed one fails until its row is lowered, so the
list only shrinks. Log calls in these files carry no emoji.

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
    "app/events/processor.py": {"project_code_blocks_to_graph": 1},
    "app/events/events.py": {"_dispatch_pdf_binary": 1, "update_record_fields": 1},
    "app/services/messaging/kafka/handlers/record.py": {
        "_index_from_blob": 1, "process_event": 2, "_download_from_signed_url": 1,
    },
    "app/services/messaging/kafka/utils/utils.py": {},
    "app/modules/transformers/vectorstore.py": {"_ask_chunk": 1},
    "app/modules/transformers/sink_orchestrator.py": {"_mark_parse_now_stored": 1},
    "app/services/messaging/redis_streams/indexing_consumer.py": {"initialize": 1, "start": 1},
    "app/services/messaging/kafka/consumer/indexing_consumer.py": {
        "initialize": 1, "start": 1, "__commit_if_appropriate": 2, "__process_bound_message": 2,
    },
    "app/utils/indexing_timing.py": {},
    "app/services/messaging/postgres/consumer.py": {},
    "app/modules/pipeline/worker.py": {"_handle_job": 1},
}


def _logs_loudly(node: ast.AST) -> bool:
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if isinstance(func, ast.Attribute) and name in _LOG_METHODS:
            return True
        if name == "log_failure":
            return True
        if name == "log" and isinstance(func, ast.Attribute) and call.args:
            level = call.args[0]  # logger.log(<level>, …): loud unless the level is a literal DEBUG or INFO
            if not (isinstance(level, ast.Attribute) and level.attr in _QUIET_LEVELS):
                return True
        if name == "log_event":
            level = next((k.value for k in call.keywords if k.arg == "level"), None)
            if isinstance(level, ast.Attribute) and level.attr in _LOUD_LEVELS:
                return True
    return False


def log_and_reraise_blocks(source: str) -> list[tuple[int, str]]:
    """(line, enclosing function) of each ``except`` block that logs at WARNING+ and raises."""
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.ExceptHandler):
                body = ast.Module(body=child.body, type_ignores=[])
                if _logs_loudly(body) and any(isinstance(n, ast.Raise) for n in ast.walk(body)):
                    found.append((child.lineno, function))
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
    counts = Counter(function for _, function in blocks)
    allowed = ALLOWED[relative]
    over = [f"{relative}:{line} in {fn}" for line, fn in blocks if counts[fn] > allowed.get(fn, 0)]
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
'''
    assert [fn for _, fn in log_and_reraise_blocks(source)] == ["a", "b", "e", "f"]
