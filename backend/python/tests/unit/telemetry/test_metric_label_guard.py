"""No metric label or event prop may say whose data it is: an email address, its domain or a name.

Every Python metric is created through ``METRICS_BACKEND.<counter|gauge|histogram>(name, help, labels, ...)``
and every product event through ``record_event(name, props)``, so the guard reads those calls from the
source and checks their label lists and prop keys.
"""

import ast
import re
from pathlib import Path

import pytest

from app.telemetry.event_buffer import IDENTIFYING_PROP

# Parses every module under app/ and harness_core/ once; slow on a loaded host.
pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN = re.compile(r"domain|email", re.IGNORECASE)
KINDS = {"counter", "gauge", "histogram"}


Scanned = tuple[list[tuple[str, int, ast.expr | None]], list[tuple[str, int, str]], int]


@pytest.fixture(scope="module")
def scanned() -> Scanned:
    """One pass over the source; only the findings are kept, never the trees (they would fill a worker)."""
    metric_calls: list[tuple[str, int, ast.expr | None]] = []
    event_keys: list[tuple[str, int, str]] = []
    event_calls = 0
    for package in ("app", "harness_core"):
        for path in sorted((ROOT / package).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            metric_calls += _metric_calls(rel, tree)
            n, keys = _event_keys(rel, tree)
            event_calls += n
            event_keys += keys
    return metric_calls, event_keys, event_calls


def _metric_calls(rel: str, tree: ast.Module) -> list[tuple[str, int, ast.expr | None]]:
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in KINDS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "METRICS_BACKEND"
        ):
            labels = node.args[2] if len(node.args) > 2 else next(
                (k.value for k in node.keywords if k.arg == "labels"), None
            )
            calls.append((rel, node.lineno, labels))
    return calls


def _event_keys(rel: str, tree: ast.Module) -> tuple[int, list[tuple[str, int, str]]]:
    if not any(isinstance(n, ast.ImportFrom) and n.module == "app.telemetry.event_buffer" for n in ast.walk(tree)):
        return 0, []
    calls, keys = 0, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "record_event":
            calls += 1
            keys += [
                (rel, node.lineno, k.value)
                for arg in node.args[1:] if isinstance(arg, ast.Dict)
                for k in arg.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
            ]
    return calls, keys


def _literal_labels(labels: ast.expr | None) -> list[str] | None:
    if isinstance(labels, (ast.List, ast.Tuple)) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in labels.elts
    ):
        return [e.value for e in labels.elts]  # type: ignore[attr-defined]
    return None


def test_the_guard_sees_the_metrics(scanned: Scanned) -> None:
    assert len(scanned[0]) > 50


def test_every_metric_declares_a_literal_label_list(scanned: Scanned) -> None:
    not_literal = [(f, line) for f, line, labels in scanned[0] if _literal_labels(labels) is None]
    assert not_literal == []


def test_no_metric_has_a_domain_or_email_label(scanned: Scanned) -> None:
    offending = [
        (f, line, names)
        for f, line, labels in scanned[0]
        if (names := _literal_labels(labels)) and any(FORBIDDEN.search(n) for n in names)
    ]
    assert offending == []


def test_no_event_is_recorded_with_an_email_domain_or_name(scanned: Scanned) -> None:
    """record_event drops these props anyway; a call that still builds one is a mistake to fix at the source."""
    _, event_keys, event_calls = scanned
    assert event_calls > 5
    assert [k for k in event_keys if IDENTIFYING_PROP.search(k[2])] == []
