"""SEC-PI-07 / WS-10: an input's file name is reduced to one no shell or tool reads as syntax (C3.08)."""

from __future__ import annotations

import re

import pytest

from harness_core.engines.durable.inputs import MAX_INPUT_NAME_BYTES, input_name
from harness_core.workspace.types import normalize_workspace_file_path

# Letters and digits of any script, and ``._-+``: nothing a POSIX shell, glob or option parser treats specially.
_SAFE = re.compile(r"[\w.+-]+")

HOSTILE = [
    ("$(rm -rf ~).pdf", "rm_-rf_.pdf"),
    ("`id`.txt", "id_.txt"),
    ("a;b|c&.csv", "a_b_c_.csv"),
    ("../../etc/passwd", "passwd"),
    ("..\\..\\boot.ini", "boot.ini"),
    ("report\x00.pdf", "report_.pdf"),
    ("line\nbreak.md", "line_break.md"),
    ("tab\there\r.md", "tab_here_.md"),
    ("-rf.txt", "rf.txt"),
    ("--help", "help"),
    (".bashrc", "bashrc"),
    ("invoice‮fdp.exe", "invoice_fdp.exe"),
    ("zero​width.md", "zero_width.md"),
    ("quote's \"name\".md", "quote_s_name_.md"),
    ("glob*?[x].md", "glob_x_.md"),
    ("{a,b}>out<in!#.md", "a_b_out_in_.md"),
    ("CON", "file_CON"),
    ("con.txt", "file_con.txt"),
    ("LPT1.log", "file_LPT1.log"),
    ("trailing.", "trailing"),
]


@pytest.mark.parametrize(("raw", "staged"), HOSTILE, ids=[repr(r) for r, _ in HOSTILE])
def test_sec_pi_07_a_hostile_name_stages_as_a_safe_one(raw: str, staged: str) -> None:
    name = input_name(raw)
    assert name == staged
    assert _SAFE.fullmatch(name), name
    assert not name.startswith(("-", ".")), name
    assert normalize_workspace_file_path(name) == name


@pytest.mark.parametrize("raw", [".", "..", "", "/", "...", "$()", "‮", "___"])
def test_sec_pi_07_a_name_with_nothing_safe_left_stages_as_input(raw: str) -> None:
    assert input_name(raw) == "input"


def test_sec_pi_07_ordinary_names_are_kept() -> None:
    for name in ("notes.md", "Q3-report_v2.pdf", "résumé.docx", "数据.xlsx", "a+b.txt"):
        assert input_name(name) == name


def test_sec_pi_07_the_same_name_always_stages_alike() -> None:
    assert input_name("$(rm -rf ~).pdf") == input_name("$(rm -rf ~).pdf")
    assert input_name("é.md") == input_name("é.md"), "NFC: one name however it was typed"


def test_sec_pi_07_a_long_name_keeps_its_extension_and_stays_distinct() -> None:
    first, second = input_name("a" * 300 + "1.pdf"), input_name("a" * 300 + "2.pdf")
    assert first != second
    for name in (first, second):
        assert name.endswith(".pdf") and len(name.encode()) <= MAX_INPUT_NAME_BYTES
        assert _SAFE.fullmatch(name)
    wide = input_name("数" * 200 + ".txt")
    assert wide.endswith(".txt") and len(wide.encode()) <= MAX_INPUT_NAME_BYTES
