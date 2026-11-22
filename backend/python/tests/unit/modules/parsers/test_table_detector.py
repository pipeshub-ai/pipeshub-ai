"""Gate settings for the pdfplumber layout model (app.modules.parsers.pdf.table_detector)."""
import importlib
import os
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


@pytest.mark.parametrize(
    ("explicit", "lane", "expected"),
    [(None, None, 4), (None, "1", 1), (None, "3", 3), (None, "0", 4), (None, "x", 4),
     ("2", "1", 2), ("0", "1", 0), ("6", None, 6)],
)
def test_threads_from_lane_env(
    monkeypatch: pytest.MonkeyPatch, fresh_td: Callable[[], ModuleType], explicit: str | None, lane: str | None, expected: int
) -> None:
    monkeypatch.delenv("PDF_TABLE_MODEL_THREADS", raising=False)
    monkeypatch.delenv("PIPESHUB_LANE_THREADS", raising=False)
    if explicit is not None:
        monkeypatch.setenv("PDF_TABLE_MODEL_THREADS", explicit)
    if lane is not None:
        monkeypatch.setenv("PIPESHUB_LANE_THREADS", lane)
    assert fresh_td()._threads() == expected


def test_explicit_threads_win(monkeypatch: pytest.MonkeyPatch, fresh_td: Callable[[], ModuleType]) -> None:
    """A setting made when the module was imported beats a lane value that arrives later."""
    monkeypatch.setenv("PDF_TABLE_MODEL_THREADS", "0")
    mod = fresh_td()
    monkeypatch.setenv("PIPESHUB_LANE_THREADS", "1")
    assert mod._threads() == 0


@pytest.mark.parametrize("raw", [None, "2", "0", "", "junk"])
def test_legacy_thread_constant_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, fresh_td: Callable[[], ModuleType], raw: str | None
) -> None:
    monkeypatch.delenv("PIPESHUB_LANE_THREADS", raising=False)
    if raw is None:
        monkeypatch.delenv("PDF_TABLE_MODEL_THREADS", raising=False)
    else:
        monkeypatch.setenv("PDF_TABLE_MODEL_THREADS", raw)
    try:
        legacy = int(os.environ.get("PDF_TABLE_MODEL_THREADS", "4"))
    except ValueError:
        with pytest.raises(ValueError):
            fresh_td()
        return
    mod = fresh_td()
    assert mod._THREADS == legacy and mod._threads() == legacy
