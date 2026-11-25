"""No test reads a backend endpoint variable with a default (tests/support/it_backends.py).

A default such as ``os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")`` dials whatever answers on that port
when the variable is unset. On a shared host that was another session's test database: our suites ran against it,
failed on its locks and wrote into it. An endpoint comes from ``it_backends.endpoint`` instead, which skips unset."""

from __future__ import annotations

import ast
import warnings

from tests.cohesion.support import PYTHON_ROOT
from tests.support.it_backends import BACKENDS

_READERS = {"get", "getenv", "setdefault"}


def _endpoint_read(node: ast.AST) -> str | None:
    """The endpoint variable a call such as ``os.environ.get("X", ...)`` or ``os.getenv("X")`` reads, if any."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _READERS):
        return None
    if not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value not in BACKENDS:
        return None
    return node.args[0].value


def _is_default(node: ast.expr) -> bool:
    return not (isinstance(node, ast.Constant) and node.value in (None, ""))


def defaulted_reads(source: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    with warnings.catch_warnings():
        # Other files' invalid escape sequences are not this check's business.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    for node in ast.walk(tree):
        if (variable := _endpoint_read(node)) is not None:
            assert isinstance(node, ast.Call)
            default = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg in ("default", "value")), None
            )
            if default is not None and _is_default(default):
                found.append((node.lineno, variable))
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            head, *rest = node.values
            if (variable := _endpoint_read(head)) is not None and any(_is_default(v) for v in rest):
                found.append((node.lineno, variable))
    return found


def _names_a_backend(source: str) -> bool:
    # The check matches only a string constant naming a backend variable, which is then in the text as written:
    # a file that never names one cannot offend, and is not parsed (parsing all ~3,800 test files took most of
    # the 30 s bound on a loaded host).
    return any(variable in source for variable in BACKENDS)


def test_no_test_dials_a_default_backend_endpoint() -> None:
    paths = sorted((PYTHON_ROOT / "tests").rglob("*.py"))
    sources = ((path, path.read_text(encoding="utf-8", errors="replace")) for path in paths)
    offenders = [
        f"{path.relative_to(PYTHON_ROOT)}:{line} {variable}"
        for path, source in sources
        if _names_a_backend(source)
        for line, variable in defaulted_reads(source)
    ]
    assert offenders == [], f"read these through tests.support.it_backends.endpoint, with no default: {offenders}"


def test_the_check_sees_each_form() -> None:
    for source in [
        'os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")',
        'int(os.environ.get("REDIS_IT_HOST", "127.0.0.1"))',
        'os.getenv("ARANGO_TEST_URL", "http://localhost:8539")',
        'os.environ.setdefault("REDIS_CLUSTER_ENDPOINTS", DEFAULT_ENDPOINTS)',
        'os.environ.get("ETCD_IT_URL") or "http://localhost:2389"',
        'os.environ.get("KAFKA_IT_BOOTSTRAP", default="localhost:29192")',
    ]:
        assert defaulted_reads(source), source
    for source in [
        'os.environ.get("NEO4J_IT_URI")',
        'os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")',
        'os.environ.get("REDIS_IT_PORT", "6389")',
        'os.environ.get("QDRANT_IT_HOST", "").strip()',
        'it_backends.endpoint("NEO4J_IT_URI")',
    ]:
        assert not defaulted_reads(source), source
