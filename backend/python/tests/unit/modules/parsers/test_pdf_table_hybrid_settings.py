"""Settings of the table-structure shadow run: defaults, bad values, and that the default path runs none of it."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.parsers.pdf import table_hybrid_flow as flow

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "table_hybrid" / "tables.pdf"
PYTHON_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch) -> None:
    for name in ("PDF_TABLE_EXTRACTION", "PDF_TABLE_HYBRID_SHADOW", "PDF_TABLE_MODEL_MAX_TABLES_PER_DOC", "PDF_TABLE_MODEL_TIMEOUT_S", "PDF_TABLE_MODEL_DOC_SECONDS"):
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    assert flow.shadow_active() is False
    assert flow.max_tables() == 300
    assert flow.model_timeout_s() == 120.0
    assert flow.doc_seconds() == 300.0


@pytest.mark.parametrize(
    ("mode", "flag", "active"),
    [("shadow", "true", True), ("shadow", "false", False), ("shadow", None, False), ("legacy", "true", False), ("evidence", "true", False), (None, "true", False)],
)
def test_the_run_needs_shadow_mode_and_the_flag(monkeypatch, mode, flag, active) -> None:
    if mode:
        monkeypatch.setenv("PDF_TABLE_EXTRACTION", mode)
    if flag:
        monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", flag)
    assert flow.shadow_active() is active


@pytest.mark.parametrize("value", ["abc", "0", "-5", ""])
def test_bad_values_use_the_default(monkeypatch, value) -> None:
    monkeypatch.setenv("PDF_TABLE_MODEL_MAX_TABLES_PER_DOC", value)
    monkeypatch.setenv("PDF_TABLE_MODEL_TIMEOUT_S", value)
    monkeypatch.setenv("PDF_TABLE_MODEL_DOC_SECONDS", value)
    assert (flow.max_tables(), flow.model_timeout_s(), flow.doc_seconds()) == (300, 120.0, 300.0)


def test_a_bad_flag_value_is_not_on(monkeypatch) -> None:
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "shadow")
    monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", "yes please")
    assert flow.shadow_active() is False


def test_valid_values_are_used(monkeypatch) -> None:
    monkeypatch.setenv("PDF_TABLE_MODEL_MAX_TABLES_PER_DOC", "12")
    monkeypatch.setenv("PDF_TABLE_MODEL_TIMEOUT_S", "30")
    monkeypatch.setenv("PDF_TABLE_MODEL_DOC_SECONDS", "45.5")
    assert (flow.max_tables(), flow.model_timeout_s(), flow.doc_seconds()) == (12, 30.0, 45.5)


def test_the_default_path_imports_no_docling_and_makes_no_collector() -> None:
    """A parse in ``legacy`` mode (and in plain ``shadow`` without the flag) loads no model library."""
    code = (
        "import sys, pathlib\n"
        "from app.modules.parsers.pdf.layout_pages import parse_pdf_layout\n"
        f"data = pathlib.Path({str(FIXTURE)!r}).read_bytes()\n"
        "pages = parse_pdf_layout(data, '/nonexistent-spool')\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('docling', 'docling_ibm_models', 'torch', 'transformers'))\n"
        "assert pages and not bad, bad\n"
        "print('ok')\n"
    )
    for env_extra in ({}, {"PDF_TABLE_EXTRACTION": "shadow"}):
        import os

        env = {**os.environ, "PYTHONPATH": str(PYTHON_ROOT), "PDF_TABLE_MODEL": "off", **env_extra}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(PYTHON_ROOT), timeout=120, check=False)
        assert out.returncode == 0 and out.stdout.strip().endswith("ok"), out.stderr[-2000:]
