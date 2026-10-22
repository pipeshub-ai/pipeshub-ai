"""Gate settings for the pdfplumber layout model (app.modules.parsers.pdf.table_detector)."""
import importlib
from collections.abc import Callable, Iterator
from types import ModuleType

import pytest

from app.modules.parsers.pdf import table_detector as td


@pytest.fixture
def fresh_td(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], ModuleType]]:
    """The module re-imported under the test's env, with no cached sessions."""
    def load() -> ModuleType:
        mod = importlib.reload(td)
        mod._sessions.clear()
        return mod
    yield load
    monkeypatch.undo()
    importlib.reload(td)


def test_bundled_gate_model_ships_with_the_code() -> None:
    assert td._GATE_MODEL_BUNDLED.is_file()
    assert td._GATE_MODEL_BUNDLED.stat().st_size > 1_000_000


def test_gate_thresholds_come_from_env(monkeypatch: pytest.MonkeyPatch, fresh_td: Callable[[], ModuleType]) -> None:
    monkeypatch.setenv("PDF_TABLE_GATE_TABLE_SCORE", "0.35")
    monkeypatch.setenv("PDF_TABLE_GATE_PICTURE_SCORE", "0.6")
    mod = fresh_td()
    assert (mod._GATE_MIN_SCORE, mod._GATE_PICTURE_MIN_SCORE) == (0.35, 0.6)


def test_gate_defaults(monkeypatch: pytest.MonkeyPatch, fresh_td: Callable[[], ModuleType]) -> None:
    monkeypatch.delenv("PDF_TABLE_GATE_TABLE_SCORE", raising=False)
    monkeypatch.delenv("PDF_TABLE_GATE_PICTURE_SCORE", raising=False)
    mod = fresh_td()
    assert (mod._GATE_MIN_SCORE, mod._GATE_PICTURE_MIN_SCORE) == (0.2, 0.3)


def test_gate_loads_the_bundled_model_unless_turned_off(monkeypatch: pytest.MonkeyPatch, fresh_td: Callable[[], ModuleType]) -> None:
    pytest.importorskip("onnxruntime")
    monkeypatch.delenv("PDF_TABLE_GATE_MODEL", raising=False)
    mod = fresh_td()
    assert mod._session("gate") is not None

    monkeypatch.setenv("PDF_TABLE_GATE_MODEL", "off")
    mod = fresh_td()
    assert mod._session("gate") is None
