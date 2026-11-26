"""The wave 1 case format, its loader and the shared fixture world (data files, the knowledge fixture).

A case is a task a user would give, the inputs it needs (data files, the knowledge fixture), what a correct outcome
looks like and a reference solution, which the scripted model plays (``wave1_reference``). The cases committed here
are the few the parity and replay tests drive; a suite that carries more cases loads them from its own directory
with the same loader (``case_dir``) over the same fixture world.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from tests.harness_replay.world import Doc

WAVE1_DIR = Path(__file__).parent / "wave1"
CASE_DIR = WAVE1_DIR / "cases"
FIXTURE_DIR = WAVE1_DIR / "fixtures"
DATA_DIR = FIXTURE_DIR / "data"
KNOWLEDGE_FILE = FIXTURE_DIR / "knowledge.yaml"

Suite = Literal["data_analysis", "office_documents", "reports"]
SUITES: tuple[Suite, ...] = ("data_analysis", "office_documents", "reports")
FileKind = Literal["xlsx", "docx", "pptx", "csv", "md"]

_ANSWER_LINE = re.compile(r"^ANSWER\s+([\w.-]+)\s*=\s*(-?[\d.]+(?:e-?\d+)?)\s*$", re.MULTILINE)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NumberCheck(_Spec):
    """One number the answer must state: within ``abs_tol`` of ``value``, or
    within ``rel_tol`` (a fraction of ``value``) when a case sets it. Both
    default to exact; a relative default would let a revenue figure be off
    by thousands."""

    name: str
    value: float
    abs_tol: float = 0.0
    rel_tol: float = 0.0


class TotalCheck(_Spec):
    """A row whose first cell is ``label`` and whose ``column`` equals the sum
    of that column over the other data rows."""

    column: str
    label: str = "Total"


class FileSpec(_Spec):
    kind: FileKind
    slides_min: int | None = None
    slides_max: int | None = None
    headings: list[str] = []
    columns: list[str] = []
    min_rows: int | None = None
    total: TotalCheck | None = None
    facts: list[str] = []


class CitationSpec(_Spec):
    """``supporting``: fixture docs that back a claim in a correct answer (a
    citation outside them is imprecise). ``required``: docs a correct answer
    must cite (coverage)."""

    supporting: list[str]
    required: list[str]
    min_precision: float = 0.75
    min_coverage: float = 1.0

    @model_validator(mode="after")
    def _required_support(self) -> CitationSpec:
        if not set(self.required) <= set(self.supporting):
            raise ValueError("every required source must also be a supporting source")
        return self


class Section(_Spec):
    heading: str
    text: str


class Slide(_Spec):
    title: str
    bullets: list[str] = []


class Table(_Spec):
    sheet: str = "Sheet1"
    header: list[str]
    rows: list[list[str | float | int]]
    total_column: str | None = None


class ReferenceDocument(_Spec):
    """What the reference program writes; exactly one of the three."""

    name: str
    sections: list[Section] = []
    slides: list[Slide] = []
    table: Table | None = None


class Sentence(_Spec):
    text: str
    cite: list[str] = []


class Reference(_Spec):
    code: str | None = None
    searches: list[str] = []
    document: ReferenceDocument | None = None
    answer: list[Sentence] = []


class Wave1Case(_Spec):
    id: str
    suite: Suite
    task: str
    inputs: list[str] = []
    knowledge: bool = False
    numbers: list[NumberCheck] = []
    file: FileSpec | None = None
    citations: CitationSpec | None = None
    # Office documents (G2C-2): the knowledge records the deliverable draws on, checked with the reports' citation
    # check but apart from the case's own outcome, since the answer is a file.
    sources: CitationSpec | None = None
    facts: list[str] = []
    rubric: str | None = None
    reference: Reference
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Wave1Case:
        if not (self.numbers or self.file or self.citations or self.facts):
            raise ValueError(f"{self.id}: a case needs at least one deterministic check")
        if self.suite == "data_analysis" and (not self.inputs or not self.reference.code):
            raise ValueError(f"{self.id}: a data case needs input files and reference code")
        if self.suite != "data_analysis" and not self.knowledge:
            raise ValueError(f"{self.id}: document and report cases read the knowledge fixture")
        if self.suite == "office_documents" and (self.file is None or self.reference.document is None):
            raise ValueError(f"{self.id}: an office case needs a file spec and a reference document")
        if self.suite == "reports" and (self.citations is None or not self.reference.answer):
            raise ValueError(f"{self.id}: a report case needs citations and a reference answer")
        if (self.suite == "office_documents") != (self.sources is not None):
            raise ValueError(f"{self.id}: office cases, and only they, name the sources their deliverable draws on")
        return self


class CaseSetError(ValueError):
    pass


@cache
def load_knowledge() -> tuple[Doc, ...]:
    raw = yaml.safe_load(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    return tuple(Doc.model_validate(d) for d in raw["docs"])


def load_suite(suite: Suite, case_dir: Path = CASE_DIR) -> list[Wave1Case]:
    raw = yaml.safe_load((case_dir / f"{suite}.yaml").read_text(encoding="utf-8"))
    cases = [Wave1Case.model_validate({**c, "suite": suite}) for c in raw["cases"]]
    for case in cases:
        missing = [name for name in case.inputs if not (DATA_DIR / name).is_file()]
        if missing:
            raise CaseSetError(f"{case.id}: missing data fixture(s) {missing}")
        for spec in (case.citations, case.sources):
            if spec is None:
                continue
            known = {d.id for d in load_knowledge()}
            unknown = set(spec.supporting) - known
            if unknown:
                raise CaseSetError(f"{case.id}: unknown knowledge doc(s) {sorted(unknown)}")
    return cases


def load_cases(suites: tuple[Suite, ...] = SUITES, case_dir: Path = CASE_DIR) -> list[Wave1Case]:
    cases = [c for s in suites for c in load_suite(s, case_dir)]
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise CaseSetError(f"duplicate case id {case.id}")
        seen.add(case.id)
    return cases


def select(suite: str | None = None, ids: list[str] | None = None, case_dir: Path = CASE_DIR) -> list[Wave1Case]:
    if suite is not None and suite not in SUITES:
        raise CaseSetError(f"'{suite}' is not a wave 1 suite. Choose one of: {', '.join(SUITES)}.")
    cases = load_cases((suite,) if suite else SUITES, case_dir)  # type: ignore[arg-type]
    if ids:
        wanted = set(ids)
        cases = [c for c in cases if c.id in wanted]
        unknown = wanted - {c.id for c in cases}
        if unknown:
            raise CaseSetError(f"unknown case id(s) {sorted(unknown)}")
    if not cases:
        raise CaseSetError("no case selected")
    return cases


def run_reference_code(case: Wave1Case) -> tuple[dict[str, float], dict[str, bytes]]:
    """Run the case's reference program the way the sandbox does (inputs under
    ``input/artifacts``, ``$OUTPUT_DIR``) on the host interpreter; return its
    ``ANSWER name=value`` lines and the files it wrote."""
    assert case.reference.code is not None
    with tempfile.TemporaryDirectory(prefix="wave1-ref-") as tmp:
        root = Path(tmp)
        (root / "input" / "artifacts").mkdir(parents=True)
        (root / "output").mkdir()
        for name in case.inputs:
            (root / "input" / "artifacts" / name).write_bytes((DATA_DIR / name).read_bytes())
        (root / "main.py").write_text(case.reference.code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "main.py"],
            cwd=root,
            env={**os.environ, "OUTPUT_DIR": str(root / "output")},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"{case.id}: reference program failed:\n{proc.stderr[-2000:]}")
        answers = {k: float(v) for k, v in _ANSWER_LINE.findall(proc.stdout)}
        files = {p.name: p.read_bytes() for p in (root / "output").iterdir() if p.is_file()}
    return answers, files


def parse_answer_lines(stdout: str) -> dict[str, float]:
    return {k: float(v) for k, v in _ANSWER_LINE.findall(stdout)}


__all__ = [
    "CASE_DIR",
    "FIXTURE_DIR",
    "DATA_DIR",
    "KNOWLEDGE_FILE",
    "SUITES",
    "CaseSetError",
    "CitationSpec",
    "FileSpec",
    "NumberCheck",
    "Reference",
    "ReferenceDocument",
    "Section",
    "Sentence",
    "Slide",
    "Suite",
    "Table",
    "TotalCheck",
    "Wave1Case",
    "load_cases",
    "load_knowledge",
    "load_suite",
    "parse_answer_lines",
    "run_reference_code",
    "select",
]
