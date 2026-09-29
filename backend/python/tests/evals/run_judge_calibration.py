"""Check the demo answer judge against labelled hard cases.

    python -m tests.evals.run_judge_calibration \
        --output reports/evals/judge-calibration.json --summary reports/evals/summary.md

Asks the judge about every case in ``answer_judge_calibration.yaml`` (one model
call per case) and compares its pass/fail decision on each claim with the
human label. Exits 1 when they agree on fewer than ``--threshold`` of the
claims (0.95 by default), and 2 when there is no model to ask: a calibration
that asked nobody must not look like one that passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

_HERE = Path(__file__).resolve().parent
_BACKEND_PYTHON = _HERE.parent.parent
if str(_BACKEND_PYTHON) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PYTHON))

from app.connectors.sources.demo.harness.answer_judge import (  # noqa: E402
    AnswerJudge,
    ClaimKind,
    JudgeResult,
)
from tests.evals.chat_models import (  # noqa: E402
    MissingModelError,
    judge_client_from_env,
)
from tests.evals.cost import run_cost  # noqa: E402

CASES_PATH = _HERE / "answer_judge_calibration.yaml"
DEFAULT_THRESHOLD = 0.95

Label = Literal["supported", "contradicted", "missing"]


class CalibrationClaim(BaseModel):
    state: str | None = None
    not_state: str | None = None
    expect: list[Label]

    @model_validator(mode="before")
    @classmethod
    def _expect_as_list(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("expect"), str):
            return {**data, "expect": [data["expect"]]}
        return data

    @model_validator(mode="after")
    def _one_side(self) -> CalibrationClaim:
        if (self.state is None) == (self.not_state is None):
            raise ValueError("a claim is either `state` or `not_state`")
        if not (self.state or self.not_state or "").strip():
            raise ValueError("a claim needs text")
        if not self.expect:
            raise ValueError("`expect` needs at least one verdict")
        if "supported" in self.expect and len(self.expect) > 1:
            raise ValueError("`supported` cannot share `expect` with another verdict")
        return self

    @property
    def text(self) -> str:
        return self.state or self.not_state or ""

    @property
    def kind(self) -> ClaimKind:
        return "must_state" if self.state is not None else "must_not_state"

    @property
    def should_pass(self) -> bool:
        stated = self.expect == ["supported"]
        return stated if self.kind == "must_state" else not stated


class CalibrationCase(BaseModel):
    id: str
    about: str = ""
    answer: str
    claims: list[CalibrationClaim]


class CalibrationSet(BaseModel):
    cases: list[CalibrationCase]

    @model_validator(mode="after")
    def _unique_ids(self) -> CalibrationSet:
        ids = [c.id for c in self.cases]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate case ids: {dupes}")
        return self


class ClaimAgreement(BaseModel):
    case_id: str
    claim: str
    kind: ClaimKind
    expected: list[Label]
    should_pass: bool
    judged_pass: bool
    verdict: str
    reasoning: str = ""
    quote: str = ""

    @property
    def agrees(self) -> bool:
        return self.should_pass == self.judged_pass


class CalibrationReport(BaseModel):
    claims: list[ClaimAgreement]
    judge_errors: list[str]
    threshold: float

    @property
    def agreement(self) -> float:
        return sum(c.agrees for c in self.claims) / len(self.claims) if self.claims else 0.0

    @property
    def exact_verdicts(self) -> float:
        return sum(c.verdict in c.expected for c in self.claims) / len(self.claims) if self.claims else 0.0

    @property
    def passed(self) -> bool:
        return bool(self.claims) and self.agreement >= self.threshold


def load_cases(path: Path = CASES_PATH) -> CalibrationSet:
    return CalibrationSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _ordered(case: CalibrationCase) -> list[CalibrationClaim]:
    """The case's claims in the order the judge reports them: must-state first."""
    return [c for c in case.claims if c.kind == "must_state"] + [c for c in case.claims if c.kind == "must_not_state"]


def _compare(case: CalibrationCase, result: JudgeResult) -> list[ClaimAgreement]:
    judged = result.claims if result.status == "judged" else []
    rows = []
    for i, claim in enumerate(_ordered(case)):
        got = judged[i] if i < len(judged) else None
        rows.append(ClaimAgreement(
            case_id=case.id,
            claim=claim.text,
            kind=claim.kind,
            expected=claim.expect,
            should_pass=claim.should_pass,
            # A judge error never agrees, whichever way the label points.
            judged_pass=got.passed if got else not claim.should_pass,
            verdict=got.verdict if got else result.status,
            reasoning=got.reasoning if got else result.detail,
            quote=got.quote if got else "",
        ))
    return rows


def calibrate(judge: AnswerJudge, cases: CalibrationSet, threshold: float = DEFAULT_THRESHOLD) -> CalibrationReport:
    rows: list[ClaimAgreement] = []
    errors: list[str] = []
    for case in cases.cases:
        must = [c.text for c in case.claims if c.kind == "must_state"]
        must_not = [c.text for c in case.claims if c.kind == "must_not_state"]
        result = judge.judge(case.answer, must, must_not)
        if result.status != "judged":
            errors.append(f"{case.id}: {result.detail}")
        rows += _compare(case, result)
    return CalibrationReport(claims=rows, judge_errors=errors, threshold=threshold)


def render_summary(report: CalibrationReport, cost_line: str) -> str:
    wrong = [c for c in report.claims if not c.agrees]
    lines = [
        "## Answer judge calibration",
        "",
        f"{'Passed' if report.passed else 'FAILED'}: the judge agreed with the labels on "
        f"{sum(c.agrees for c in report.claims)} of {len(report.claims)} claims "
        f"({report.agreement:.0%}; needs {report.threshold:.0%}). "
        f"Exact verdicts matched on {report.exact_verdicts:.0%}.",
        "",
        cost_line,
    ]
    if report.judge_errors:
        lines += ["", "Judge errors (each counts as a disagreement):", *[f"- {e}" for e in report.judge_errors]]
    if wrong:
        lines += ["", "| Case | Claim | Expected | Judge said | Why |", "| --- | --- | --- | --- | --- |"]
        for c in wrong:
            why = " ".join(c.reasoning.split())[:200].replace("|", "/")
            lines.append(f"| {c.case_id} | {c.claim} | {'/'.join(c.expected)} | {c.verdict} | {why} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", type=Path, default=CASES_PATH)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--output", type=Path, help="write the per-claim result as JSON")
    ap.add_argument("--summary", type=Path, help="append a Markdown summary")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    try:
        client, provider, model = judge_client_from_env()
    except MissingModelError as exc:
        print(f"No judge to calibrate: {exc}", file=sys.stderr)
        return 2

    report = calibrate(AnswerJudge(client), cases, args.threshold)
    cost = run_cost(model, client.input_tokens, client.output_tokens)
    summary = render_summary(report, f"{provider} / {model}, {client.calls} calls. {cost.render()}")
    print(summary)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": provider,
            "model": model,
            "agreement": report.agreement,
            "exact_verdicts": report.exact_verdicts,
            "threshold": report.threshold,
            "passed": report.passed,
            "calls": client.calls,
            "input_tokens": client.input_tokens,
            "output_tokens": client.output_tokens,
            "cost_usd": cost.usd,
            "judge_errors": report.judge_errors,
            "claims": [c.model_dump() | {"agrees": c.agrees} for c in report.claims],
        }
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write("\n" + summary)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
