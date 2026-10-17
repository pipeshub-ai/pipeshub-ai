"""Run a golden-eval set against a real model and report what it cost.

    python -m tests.evals.run_scheduled_evals --set nightly \
        --output reports/evals/nightly.json --summary reports/evals/summary.md

Writes a JSON result, appends a Markdown summary, and compares the run with a
committed baseline so a fall in pass rate is visible rather than buried in a
log. Exits non-zero when the run measured nothing: no model, no cases, or every
case skipped. A green run that tested nothing is worse than a red one, because
nobody investigates it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_BACKEND_PYTHON = _HERE.parent.parent
if str(_BACKEND_PYTHON) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PYTHON))

from tests.evals.case_sets import SET_NAMES, select_cases  # noqa: E402
from tests.evals.cost import RunCost, monthly_projection, run_cost  # noqa: E402
from tests.evals.live_harness import EvalReport, run_golden_evals  # noqa: E402
from tests.evals.live_runner import (  # noqa: E402
    MAX_TURNS,
    BadContextLengthError,
    MissingModelError,
    UsageTally,
    build_chat_model,
    context_length_from_env,
    make_run_agent,
    prompt_tier,
    resolve_model,
)

SCHEMA_VERSION = 1

# A drop of more than this share of the baseline's pass rate is called out.
# Answer quality moves in steps as cases are added, so a single case failing in
# a four-case set is a 25% fall — worth a look, not worth blocking a merge.
PASS_RATE_DROP_THRESHOLD = 0.10


@dataclass(frozen=True)
class Comparison:
    rows: list[str]
    mismatches: list[str]
    regressed: bool


def build_result(
    *,
    report: EvalReport,
    case_set: str,
    provider: str,
    model: str,
    tier: str,
    cost: RunCost,
    tally: UsageTally,
    started_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "answer_quality",
        "started_at": started_at.isoformat(),
        "environment": {
            "case_set": case_set,
            "provider": provider,
            "model": model,
            "prompt_tier": tier,
            "max_turns": MAX_TURNS,
        },
        "metrics": {
            "cases_total": report.total,
            "cases_ran": report.ran,
            "cases_passed": report.passed,
            "cases_skipped": report.skipped,
            "pass_rate": report.pass_rate,
            "requests": tally.requests,
            "input_tokens": tally.input_tokens,
            "output_tokens": tally.output_tokens,
            "cost_usd": cost.usd,
        },
        "cases": [
            {
                "id": r.case_id,
                "passed": r.passed,
                "skipped": r.skipped,
                "failures": list(r.failures),
                "first_tool": r.trace.first_tool,
                "tool_calls": list(r.trace.tool_calls),
                "confidence": r.trace.confidence,
                "answer_chars": len(r.trace.final_answer or ""),
            }
            for r in report.results
        ],
    }


def compare_with_baseline(baseline: dict[str, Any], current: dict[str, Any]) -> Comparison:
    """Pass rate now against pass rate then, when the runs are comparable.

    Two runs of different case sets or different models are not a like-for-like
    comparison: the numbers are shown, and no verdict is given. This mirrors
    what integration-tests/perf/compare.py does for the performance benchmarks,
    kept separate because a pass rate and a latency percentile share nothing
    but the idea.
    """
    mismatches = []
    for label, key in (
        ("case set", "case_set"),
        ("model", "model"),
        ("provider", "provider"),
        # A different tier is a different system prompt, so the two runs did
        # not measure the same thing.
        ("prompt tier", "prompt_tier"),
    ):
        was = (baseline.get("environment") or {}).get(key)
        now = (current.get("environment") or {}).get(key)
        if was != now:
            mismatches.append(f"{label}: baseline {was!r}, this run {now!r}")

    base_m = baseline.get("metrics") or {}
    cur_m = current.get("metrics") or {}
    base_rate = base_m.get("pass_rate")
    cur_rate = cur_m.get("pass_rate")
    rows = [
        "| Measure | Baseline | This run |",
        "| --- | --- | --- |",
        f"| Cases that passed | {base_m.get('cases_passed', 'n/a')}/{base_m.get('cases_ran', 'n/a')} "
        f"| {cur_m.get('cases_passed', 'n/a')}/{cur_m.get('cases_ran', 'n/a')} |",
        f"| Pass rate | {_pct(base_rate)} | {_pct(cur_rate)} |",
    ]
    regressed = False
    if isinstance(base_rate, (int, float)) and isinstance(cur_rate, (int, float)) and not mismatches:
        regressed = cur_rate < base_rate - PASS_RATE_DROP_THRESHOLD
    return Comparison(rows=rows, mismatches=mismatches, regressed=regressed)


def _pct(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.0%}"


def projection_line(case_set: str, cost: RunCost) -> str:
    """What this run's cost means over a month on its own schedule."""
    if cost.usd is None:
        return ""
    if case_set == "nightly":
        monthly = monthly_projection(cost.usd, 0.0)
        return f"At this cost every night, the nightly set is about ${monthly:.2f} a month."
    monthly = monthly_projection(0.0, cost.usd)
    return f"At this cost every week, the full set is about ${monthly:.2f} a month."


def render_summary(
    result: dict[str, Any], cost: RunCost, comparison: Comparison | None, baseline_note: str
) -> str:
    env = result["environment"]
    m = result["metrics"]
    lines = [
        f"### Answer quality — {env['case_set']} set on {env['model']}",
        "",
        f"Prompt tier: {env.get('prompt_tier', 'unknown')} — "
        "the prompt this model gets in the product.",
        "",
        f"{m['cases_passed']} of {m['cases_ran']} cases passed ({_pct(m['pass_rate'])}).",
        "",
        cost.render(),
        "",
    ]
    projection = projection_line(env["case_set"], cost)
    if projection:
        lines += [projection, ""]
    failed = [c for c in result["cases"] if not c["passed"] and not c["skipped"]]
    if failed:
        lines.append("Cases that failed:")
        lines.append("")
        for case in failed:
            lines.append(f"- `{case['id']}`: {', '.join(case['failures']) or 'no reason recorded'}")
        lines.append("")
    if comparison is None:
        lines += [baseline_note, ""]
        return "\n".join(lines)
    lines += [*comparison.rows, ""]
    if comparison.mismatches:
        lines += [
            "Not judged — these runs are not like for like:",
            "",
            *[f"- {m_}" for m_ in comparison.mismatches],
            "",
        ]
    elif comparison.regressed:
        lines += [
            "Fewer cases passed than in the baseline. Read the failures above, "
            "then decide whether the prompt, the model or the case is wrong.",
            "",
        ]
    else:
        lines += ["No fall against the baseline.", ""]
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> tuple[dict[str, Any], RunCost, str]:
    # Resolve the provider first: the key must be the one that belongs to the
    # provider actually being called.
    provider, model, api_key = resolve_model(args.provider, args.model)
    cases = select_cases(args.set)
    context_length = context_length_from_env()
    tier = prompt_tier(provider, model, context_length)
    chat_model = build_chat_model(provider, model, api_key)
    tally = UsageTally()
    started_at = datetime.now(timezone.utc)
    report = await run_golden_evals(
        model=model,
        cases=cases,
        run_agent=make_run_agent(
            chat_model, model, tally, provider=provider, context_length=context_length
        ),
    )
    cost = run_cost(model, tally.input_tokens, tally.output_tokens)
    result = build_result(
        report=report,
        case_set=args.set,
        provider=provider,
        model=model,
        tier=tier,
        cost=cost,
        tally=tally,
        started_at=started_at,
    )
    return result, cost, report.render()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", choices=SET_NAMES, default="nightly")
    parser.add_argument("--provider", default=None, help="overrides EVAL_PROVIDER")
    parser.add_argument("--model", default=None, help="overrides EVAL_MODEL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit non-zero when fewer cases pass than in the baseline",
    )
    args = parser.parse_args(argv)

    try:
        result, cost, rendered = asyncio.run(_run(args))
    except (MissingModelError, BadContextLengthError) as exc:
        print(f"This run measured nothing: {exc}", file=sys.stderr)
        return 2

    print(rendered)

    metrics = result["metrics"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    comparison: Comparison | None = None
    baseline_note = "No baseline to compare with yet."
    if args.baseline and args.baseline.exists():
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        if baseline.get("placeholder"):
            baseline_note = baseline.get(
                "note", f"`{args.baseline}` is a placeholder, so there is nothing to compare with."
            )
        else:
            comparison = compare_with_baseline(baseline, result)

    summary = render_summary(result, cost, comparison, baseline_note)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(summary)
    print(summary)

    if metrics["cases_ran"] == 0:
        print(
            "No case actually ran, so this run is not evidence of anything. "
            "Check the model settings above and the failures in the log.",
            file=sys.stderr,
        )
        return 2
    if comparison and comparison.regressed and args.fail_on_regression:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
