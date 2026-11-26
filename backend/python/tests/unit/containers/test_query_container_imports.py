"""The query container must not pull in the cross-encoder stack.

A reranker service used to be built here but nothing ever called it; its
import of sentence_transformers cost ~230 MB in every process that imported
the container.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code], cwd=_ROOT, capture_output=True, text=True, timeout=300,
    )


@pytest.mark.timeout(300)
def test_query_container_does_not_import_sentence_transformers() -> None:
    r = _run(
        "import sys; import app.containers.query;"
        "sys.exit(1 if 'sentence_transformers' in sys.modules else 0)"
    )
    assert r.returncode == 0, r.stderr[-2000:]


@pytest.mark.timeout(300)
def test_container_has_no_reranker_provider() -> None:
    r = _run(
        "import sys; from app.containers.query import QueryAppContainer as C;"
        "sys.exit(1 if hasattr(C, 'reranker_service') else 0)"
    )
    assert r.returncode == 0, r.stderr[-2000:]
