"""The OCR page cap lives in a module that does not import the model stack."""
from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

from app.modules.parsers.pdf import ocr_limits

if TYPE_CHECKING:
    import pytest


def test_the_default_cap_and_the_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ocr_limits.MAX_PAGES_ENV, raising=False)
    assert ocr_limits.vlm_ocr_max_pages() == ocr_limits.DEFAULT_MAX_PAGES == 500
    monkeypatch.setenv(ocr_limits.MAX_PAGES_ENV, "12")
    assert ocr_limits.vlm_ocr_max_pages() == 12
    monkeypatch.setenv(ocr_limits.MAX_PAGES_ENV, "0")
    assert ocr_limits.vlm_ocr_max_pages() == 1


def test_the_strategy_module_re_exports_the_same_objects() -> None:
    from app.modules.parsers.pdf import vlm_ocr_strategy

    assert vlm_ocr_strategy.vlm_ocr_max_pages is ocr_limits.vlm_ocr_max_pages
    assert vlm_ocr_strategy.MAX_PAGES_ENV == ocr_limits.MAX_PAGES_ENV


def test_importing_the_cap_does_not_load_the_model_stack() -> None:
    code = "import sys; import app.modules.parsers.pdf.ocr_limits; sys.exit(int('app.modules.parsers.pdf.vlm_ocr_strategy' in sys.modules))"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
