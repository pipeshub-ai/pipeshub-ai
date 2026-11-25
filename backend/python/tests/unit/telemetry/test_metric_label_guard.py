"""No metric may carry a label that says whose series it is: an email address or its domain.

Every Python metric is created through ``METRICS_BACKEND.<counter|gauge|histogram>(name, help, labels, ...)``,
so the guard reads those calls from the source and checks their label lists.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN = re.compile(r"domain|email", re.IGNORECASE)
KINDS = {"counter", "gauge", "histogram"}


def _metric_calls() -> list[tuple[str, int, ast.expr | None]]:
    calls = []
    for package in ("app", "harness_core"):
        for path in sorted((ROOT / package).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
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
                    calls.append((str(path.relative_to(ROOT)), node.lineno, labels))
    return calls


def _literal_labels(labels: ast.expr | None) -> list[str] | None:
    if isinstance(labels, (ast.List, ast.Tuple)) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in labels.elts
    ):
        return [e.value for e in labels.elts]  # type: ignore[attr-defined]
    return None


def test_the_guard_sees_the_metrics() -> None:
    assert len(_metric_calls()) > 50


def test_every_metric_declares_a_literal_label_list() -> None:
    not_literal = [(f, line) for f, line, labels in _metric_calls() if _literal_labels(labels) is None]
    assert not_literal == []


def test_no_metric_has_a_domain_or_email_label() -> None:
    offending = [
        (f, line, names)
        for f, line, labels in _metric_calls()
        if (names := _literal_labels(labels)) and any(FORBIDDEN.search(n) for n in names)
    ]
    assert offending == []
