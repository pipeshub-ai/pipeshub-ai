"""QAFINAL-FU-ERASE: the durable lane's erasure world check (deployment/e2e/erase_world.py) is wired and its file
fixture is a real PDF. The running lane proves the rest (EW-1..EW-6 on a stack); these keep the wiring from drifting."""

from __future__ import annotations

import importlib.util
import io
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
E2E = REPO / "deployment" / "e2e"


@pytest.fixture(scope="module")
def erase_world():  # noqa: ANN201 - a module loaded by its path
    spec = importlib.util.spec_from_file_location("e2e_erase_world", E2E / "erase_world.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qafinal_erase_1_the_uploaded_file_is_a_pdf_whose_text_holds_the_word(erase_world) -> None:
    """The attachment route takes PDF and images only; the file's word must be in its text layer, where the
    query service's parser and the model read it."""
    pdfplumber = pytest.importorskip("pdfplumber")
    data = erase_world.pdf(["Harbour tide ledger (east).", "The code is ewfileabc123."])
    with pdfplumber.open(io.BytesIO(data)) as document:
        text = "".join(page.extract_text() or "" for page in document.pages)
    assert "ewfileabc123" in text
    assert "Harbour tide ledger east." in text


def test_qafinal_erase_2_every_check_the_docstring_names_is_made_and_reported(erase_world) -> None:
    source = (E2E / "erase_world.py").read_text()
    documented = set(re.findall(r"^(EW-\d)  ", erase_world.__doc__, re.MULTILINE))
    made = set(re.findall(r'check\("(EW-\d)"', source))
    assert documented == made == {f"EW-{n}" for n in range(1, 7)}


def test_qafinal_erase_3_the_durable_lane_runs_it_after_its_specs_and_a_stack_left_up_can_too() -> None:
    run_sh = (E2E / "run.sh").read_text()
    durable = run_sh[run_sh.index("\n  durable)\n"):run_sh.index("\n  erase-world)\n")]
    assert durable.index("run_durable_specs") < durable.index("run_erase_world")
    assert 'python3 "$HERE/erase_world.py" "$WORK"' in run_sh
    assert "erase-world RUN" in run_sh
