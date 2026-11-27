"""PX-3 output identity: the structured-format parsers produce what ``origin/main`` produced.

``fixtures/px3_golden.json`` was written on ``origin/main`` (5f4b1349c9) by
``tests/perf/parse_bench/px3_golden.py``, before any PX-3 change, and run twice there to prove it
stable. Every entry is the sha256 of the canonical dump (block ids renamed by first appearance, since
uuid4 ids differ per run), the block and group counts, the metadata (``document_count``,
structural-fallback ``parser``/``template_dialect``) or the error class and code.

The hash-seed matrix runs the generator in a fresh interpreter per ``PYTHONHASHSEED`` (0, 1 and
random): the JSON walk builds a ``set`` (``json_parser.py``), so an order that leaks into output would
differ between interpreters, and an in-process run cannot see that.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.perf.parse_bench import golden, px3_golden

GOLDEN = Path(__file__).parent / "fixtures" / "px3_golden.json"
PYTHON_ROOT = Path(__file__).resolve().parents[4]


def _reference() -> dict[str, dict]:
    return json.loads(GOLDEN.read_text())


def test_the_golden_names_every_case() -> None:
    names = {f"json/{n}" for n in px3_golden.JSON_CASES} | {f"yaml/{n}" for n in px3_golden.YAML_CASES}
    names |= {f"html/{n}" for n in px3_golden.HTML_CASES} | {f"xlsx/{n}" for n in px3_golden.EXCEL_CASES}
    assert set(_reference()) == names


def test_output_equals_main_after_id_normalisation() -> None:
    assert golden.compare(_reference(), px3_golden.run()) == []
    # metadata, document_count, fallback flag and error codes are part of the comparison too
    current = px3_golden.run()
    for name, entry in _reference().items():
        assert entry.get("metadata") == current[name].get("metadata"), name
        assert entry.get("error") == current[name].get("error"), name


@pytest.mark.parametrize("seed", ["0", "1", "random"])
def test_output_independent_of_hash_seed(seed: str, tmp_path: Path) -> None:
    out = tmp_path / "run.json"  # the app's loggers write to stdout, so the result goes to a file
    done = subprocess.run(
        [sys.executable, "-m", "tests.perf.parse_bench.px3_golden", "--out", str(out)],
        capture_output=True, text=True, timeout=240, cwd=PYTHON_ROOT,
        env={**os.environ, "PYTHONHASHSEED": seed},
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert golden.compare(_reference(), json.loads(out.read_text())) == []


def test_the_comparison_sees_a_changed_block_and_a_changed_error_code() -> None:
    reference = _reference()
    changed = json.loads(json.dumps(reference))
    changed["json/object"]["sha256"] = "0" * 64
    first_error = next(k for k, v in reference.items() if v.get("error"))
    changed[first_error]["error"] = "ParseError:other"
    problems = golden.compare(reference, changed)
    assert any(p.startswith("json/object") for p in problems)
    assert any(p.startswith(first_error) for p in problems)
