"""The gate's size and off-switch refusals follow the reader the dispatch picks, code included:
a cell main read is never refused by a rule for a type that does not read it."""
from __future__ import annotations

import pytest

from app.config import file_types
from tests.unit.config.file_types import delta_rules
from tests.unit.config.file_types import gen_pinned_decisions as gen

_PINNED = gen.load()
_NEW_TYPES = ("msg", "odt", "rtf", "xlsm", "log", "cfg", "docm", "dotx", "potx")


def _main_read_rows() -> list[list[object]]:
    return [
        r for r in _PINNED["rows"]
        if delta_rules.main_indexed(r) and all(v is None or isinstance(v, str) for v in r[:2])
    ]


def _gate(row: list[object], **kw: object) -> file_types.GateDecision:
    ext, mime, name, record_type = row[:4]
    is_code_file = record_type == "CODE_FILE"
    code_ext = name.rsplit(".", 1)[-1].lower() if is_code_file and "." in name else None  # type: ignore[union-attr]
    return file_types.gate_decision(
        mime=mime, extension=ext, record_name=name, is_code_file=is_code_file,  # type: ignore[arg-type]
        code_file_extension=code_ext, **kw,
    )


@pytest.mark.parametrize("disabled", _NEW_TYPES)
def test_turning_off_a_new_type_refuses_no_cell_main_read_with_another_reader(disabled: str) -> None:
    refused = [
        tuple(r[:4]) for r in _main_read_rows()
        if r[0] not in (None, "unknown") and _gate(r, disabled=frozenset({disabled})).refusal == "disabled"
        and not (disabled in delta_rules.NEW_TYPES and f"{disabled}" == str(r[0]).lower().lstrip(".")
                 and gen.case_key(r) in delta_rules.EXISTING_CELL_EXCEPTIONS)
    ]
    assert refused == []


def test_a_log_main_read_as_code_is_read_as_code() -> None:
    decision = _gate(["log", "application/javascript", "file.log", "FILE"], disabled=frozenset({"log"}))
    assert decision.supported
    decision = _gate(["cfg", "text/x-c", "file.cfg", "FILE"], disabled=frozenset({"cfg"}))
    assert decision.supported


@pytest.mark.parametrize(
    ("extension", "mime", "off"),
    [("py", "text/x-log", "log"), ("js", "text/x-cfg", "cfg"), ("py", "text/x-cfg", "cfg")],
)
def test_a_source_file_carrying_a_log_mime_is_judged_by_its_own_type(extension: str, mime: str, off: str) -> None:
    decision = file_types.gate_decision(
        mime=mime, extension=extension, record_name=f"a.{extension}", is_code_file=False,
        code_file_extension=None, disabled=frozenset({off}),
    )
    assert decision.supported
    assert decision.file_type is not None and decision.file_type.key == extension
    refused = file_types.gate_decision(
        mime=mime, extension=extension, record_name=f"a.{extension}", is_code_file=False,
        code_file_extension=None, disabled=frozenset({extension}),
    )
    assert refused.refusal == "disabled"
