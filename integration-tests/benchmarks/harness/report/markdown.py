"""Rendering the run summary as markdown (CI job summary) and failures as CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from benchmarks.harness.models import QuestionScore
from benchmarks.harness.report.summary import PairwiseTest, Rate, RunSummary, SystemSummary


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.1f}"


def _rate(rate: Rate | None) -> str:
    if rate is None:
        return "–"
    return f"{rate.value * 100:.1f} ({rate.low * 100:.1f}–{rate.high * 100:.1f})"


def _num(value: float | None, digits: int = 0) -> str:
    return "–" if value is None else f"{value:,.{digits}f}"


def _money(value: float | None) -> str:
    return "–" if value is None else f"${value:.4f}"


def _memory_suspect(s: SystemSummary) -> str:
    if s.memory_suspect_rate is None:
        return "–"
    return f"{s.memory_suspect} ({s.memory_suspect_rate.value * 100:.1f}%)"


def _board(systems: Sequence[SystemSummary]) -> list[str]:
    rows = [
        "| System | FRAMES acc % (95% CI) | Grounded acc % (95% CI) | Memory-suspect | Strict % "
        "| All gold in context % | Context recall % "
        "| Citation integrity % | ALCE recall % | Correct ∧ grounded % | p95 latency s "
        "| LLM calls / q | Input tok / q | Output tok / q | Cost / q | Cost / correct |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in sorted(systems, key=lambda x: -(x.accuracy.value if x.accuracy else -1)):
        p95 = "–" if s.latency_p95_ms is None else f"{s.latency_p95_ms / 1000:.1f}"
        rows.append(
            f"| {s.system} | {_rate(s.accuracy)} | {_rate(s.grounded_accuracy)} | {_memory_suspect(s)} "
            f"| {_rate(s.strict_accuracy)} | {_rate(s.all_gold_in_context)} "
            f"| {_rate(s.context_recall)} | {_pct(s.citation_integrity)} | {_pct(s.alce_recall)} "
            f"| {_pct(s.correct_and_grounded)} | {p95} | {_num(s.llm_calls_per_question, 1)} "
            f"| {_num(s.input_tokens_per_question)} | {_num(s.output_tokens_per_question)} "
            f"| {_money(s.cost_per_question_usd)} | {_money(s.cost_per_correct_usd)} |",
        )
    return rows


def _breakdown_table(title: str, systems: Sequence[SystemSummary], attr: str) -> list[str]:
    labels = sorted({label for s in systems for label in getattr(s, attr)})
    if not labels:
        return []
    lines = [f"### {title}", "", "| Label | " + " | ".join(s.system for s in systems) + " |", "|---" * (len(systems) + 1) + "|"]
    for label in labels:
        cells = [_pct(getattr(s, attr).get(label)) for s in systems]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return [*lines, ""]


_SPLIT_METRICS = (
    ("FRAMES acc %", "accuracy_by_split"),
    ("Grounded acc %", "grounded_accuracy_by_split"),
    ("Memory-suspect %", "memory_suspect_rate_by_split"),
)


def _by_split(systems: Sequence[SystemSummary]) -> list[str]:
    """Only when the run spans more than one split: a single-split run
    already says it all on the board."""
    splits = sorted({split for s in systems for split in s.accuracy_by_split})
    if len(splits) < 2:
        return []
    lines = [
        "### By split (95% CI)", "",
        "| Split | Metric | " + " | ".join(s.system for s in systems) + " |",
        "|---|---" + "|---" * len(systems) + "|",
    ]
    for split in splits:
        for label, attr in _SPLIT_METRICS:
            cells = [_rate(getattr(s, attr).get(split)) for s in systems]
            lines.append(f"| {split} | {label} | " + " | ".join(cells) + " |")
    return [*lines, ""]


def _failures(systems: Sequence[SystemSummary]) -> list[str]:
    lines = []
    for s in systems:
        if s.failures:
            total = sum(s.failures.values())
            lines += [f"**{s.system}** ({total} wrong answers)", ""]
            lines += [f"- `{name}`: {count}" for name, count in sorted(s.failures.items(), key=lambda kv: -kv[1])]
            lines.append("")
    return ["### Failure signatures", "", *lines] if lines else []


def _evidence_support(systems: Sequence[SystemSummary]) -> list[str]:
    verified = [s for s in systems if s.support_coverage]
    if not verified:
        return []
    lines = [
        "### Evidence verification",
        "",
        "Every answer the primary judge marked correct is checked against the context its system "
        "showed the answering model. *Memory-suspect*: judged correct, but the facts it depends on "
        "are not in that context. A system shown no context (closed book) lands every correct answer there.",
        "",
        "| System | Supported | Partial | Memory-suspect | No evidence | Unparseable | Verified % |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in verified:
        lines.append(
            f"| {s.system} | {s.supported} | {s.partial} | {s.memory_suspect} | {s.no_evidence} "
            f"| {s.support_unparseable} | {_pct(s.support_coverage)} |",
        )
    lines.append("")
    suspects = [s for s in verified if s.memory_suspect_questions]
    if suspects:
        lines += ["Memory-suspect questions:", ""]
        lines += [f"- **{s.system}**: {', '.join(s.memory_suspect_questions)}" for s in suspects]
        lines.append("")
    return lines


def _pairwise(title: str, tests: Sequence[PairwiseTest]) -> list[str]:
    if not tests:
        return []
    lines = [f"### {title}", "", "| A | B | A only | B only | p |", "|---|---|---|---|---|"]
    lines += [f"| {t.a} | {t.b} | {t.a_only} | {t.b_only} | {t.p_value:.4f} |" for t in tests]
    return [*lines, ""]


def render_report(summary: RunSummary) -> str:
    meta = summary.meta
    status = "VALID" if summary.valid else "**INVALID — disallowed tool calls detected**"
    lines = [f"## FRAMES benchmark — `{summary.run_id}`", "", f"Status: {status}", ""]
    if meta:
        lines += [
            f"- Dataset `{meta.dataset_revision[:12]}`, split sha `{meta.split_sha256[:12]}`, corpus `{(meta.corpus_version or '–')[:12]}`",
            f"- Snapshot {meta.snapshot:%Y-%m-%d}, git `{(meta.git_sha or '–')[:12]}`",
            "- Models: " + ", ".join(f"{role}={label}" for role, label in sorted(meta.models.items())),
            "",
        ]
    if summary.violations:
        lines += ["### Policy violations", "", *[f"- `{v}`" for v in summary.violations], ""]
    lines += ["### Board", "", *_board(summary.systems), ""]
    lines += _by_split(summary.systems)
    lines += _breakdown_table("Accuracy by reasoning type", summary.systems, "by_reasoning_type")
    lines += _breakdown_table("Accuracy by gold-article count", summary.systems, "by_gold_count")
    lines += _failures(summary.systems)
    lines += _evidence_support(summary.systems)
    lines += _pairwise("Paired comparisons (exact McNemar)", summary.pairwise)
    lines += _pairwise("Paired comparisons on grounded correctness (exact McNemar)", summary.pairwise_grounded)
    kappas = [f"{s.system}: {s.judge_agreement_kappa:.3f}" for s in summary.systems if s.judge_agreement_kappa is not None]
    if kappas:
        lines += ["Judge agreement (Cohen's κ, primary vs secondary): " + ", ".join(kappas), ""]
    return "\n".join(lines)


def failures_csv(scores: Sequence[QuestionScore]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "system", "question_id", "repeat", "failure", "correct", "strict_label",
        "context_recall", "tool_waves", "searches", "fetches", "missing_gold",
    ])
    for s in sorted(scores, key=lambda x: (x.system, x.question_id, x.repeat)):
        if s.failure:
            writer.writerow([
                s.system, s.question_id, s.repeat, s.failure, s.correct, s.strict_label,
                s.context_recall, s.tool_waves, s.n_searches, s.n_fetches, " ".join(s.missing_gold),
            ])
    return buffer.getvalue()
