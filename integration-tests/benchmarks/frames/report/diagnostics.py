"""Per-question diagnosis for agent systems: why each answer went wrong,
using the other systems on the same questions as controls.

- `retrieval_miss_vs_baseline`: a gold article the agent never had in context
  was in context for a baseline reading the same (PipesHub) index — the
  agent's search/loop, not the index, lost it.
- `index_miss`: only a system on the standard-chunked index found it —
  points at PipesHub parsing/chunking/embedding.
- `retrieval_miss_all`: no system found it (hard retrieval, or a corpus gap).
- `reasoning_miss`: every gold article was in context and the answer was
  still wrong (`oracle_correct` says whether the model can answer at all).
- `run_error`: the run failed outright.

Independent of the category, `faults` lists run-quality problems worth a
look even on correct answers (loops, failed tools, the turn cap, outliers).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence

from pydantic import BaseModel, Field

from benchmarks.frames.models import Prediction, QuestionScore

_FAILED_TOOL_STATUSES = frozenset({"failed", "blocked", "error"})
_OUTLIER_PERCENTILE = 0.95


class QuestionDiagnosis(BaseModel):
    system: str
    question_id: str
    repeat: int
    correct: bool | None
    category: str | None
    oracle_correct: bool | None = None
    missing_gold: list[str] = Field(default_factory=list)
    # Missing gold URL -> systems that did have it in context.
    found_by: dict[str, list[str]] = Field(default_factory=dict)
    faults: list[str] = Field(default_factory=list)
    tool_calls: int = 0
    tool_waves: int = 0
    input_tokens: int | None = None
    latency_ms: int = 0
    conversation_id: str | None = None
    failure_signature: str | None = None


def _threshold(values: Sequence[float]) -> float | None:
    ordered = sorted(values)
    return ordered[int(_OUTLIER_PERCENTILE * (len(ordered) - 1))] if len(ordered) >= 20 else None


def _faults(p: Prediction, s: QuestionScore, token_p95: float | None, latency_p95: float | None) -> list[str]:
    faults: list[str] = []
    if p.error is not None:
        faults.append(f"error:{p.error.kind}")
    if p.error is None and not p.answer.strip():
        faults.append("empty_answer")
    if p.error is None and p.answer.strip() and not p.citations:
        faults.append("no_citations")
    if p.policy_violations:
        faults.append("policy_violation")
    if p.trace is not None:
        calls = p.trace.root_tool_calls
        seen = Counter((c.name, c.args_json.strip()) for c in calls)
        if any(n > 1 for n in seen.values()):
            faults.append("repeated_identical_tool_call")
        statuses = {(c.status or "").lower() for c in p.trace.tool_calls}
        if "blocked" in statuses:
            faults.append("tool_blocked")
        if statuses & (_FAILED_TOOL_STATUSES - {"blocked"}):
            faults.append("tool_failed")
        stats = p.trace.run_stats
        if stats is not None and stats.hit_turn_cap:
            faults.append("turn_cap")
        if stats is not None:
            if stats.completion_gate_nudges:
                faults.append("empty_model_response")
            if stats.auxiliary_llm_calls:
                faults.append("context_compacted")
            if stats.agent_error:
                faults.append("agent_error")
        if p.trace.frame_counts.get("status"):
            faults.append("llm_retry")
        if p.trace.frame_counts.get("CUSTOM:tool_unavailable"):
            faults.append("tool_unavailable")
        if any(e.status == "error" for e in p.trace.retrieval_events):
            faults.append("retrieval_error")
    if token_p95 is not None and (s.input_tokens or 0) > token_p95:
        faults.append("token_outlier")
    if latency_p95 is not None and s.latency_ms > latency_p95:
        faults.append("latency_outlier")
    return faults


def diagnose(
    target: str,
    scores: Sequence[QuestionScore],
    predictions: Mapping[tuple[str, int, int], Prediction],
    index_of: Mapping[str, str],
    gold_for: Callable[[int], list[str]],
) -> list[QuestionDiagnosis]:
    """`index_of` maps each system to the index it reads ("pipeshub",
    "standard", or "none"); `target` is the agent system under diagnosis."""
    context: dict[str, dict[str, set[str]]] = defaultdict(dict)
    correct: dict[tuple[str, int], list[bool]] = defaultdict(list)
    for s in scores:
        if s.context_recall is not None:
            gold = set(gold_for(s.question_id))
            context[s.question_id].setdefault(s.system, set()).update(gold - set(s.missing_gold))
        if s.correct is not None:
            correct[(s.system, s.question_id)].append(s.correct)
    own = [s for s in scores if s.system == target]
    token_p95 = _threshold([float(s.input_tokens) for s in own if s.input_tokens is not None])
    latency_p95 = _threshold([float(s.latency_ms) for s in own])
    oracle = next((name for name, index in index_of.items() if index == "oracle"), None)

    out: list[QuestionDiagnosis] = []
    for s in own:
        p = predictions.get((s.system, s.question_id, s.repeat))
        if p is None:
            continue
        found_by = {
            url: sorted(
                name for name, urls in context[s.question_id].items()
                if name != target and url in urls and index_of.get(name) in ("pipeshub", "standard")
            )
            for url in s.missing_gold
        }
        oracle_votes = correct.get((oracle, s.question_id)) if oracle else None
        out.append(QuestionDiagnosis(
            system=s.system, question_id=s.question_id, repeat=s.repeat, correct=s.correct,
            category=_category(s, p, found_by, index_of),
            oracle_correct=None if not oracle_votes else sum(oracle_votes) > len(oracle_votes) / 2,
            missing_gold=list(s.missing_gold), found_by={u: v for u, v in found_by.items() if v},
            faults=_faults(p, s, token_p95, latency_p95),
            tool_calls=s.n_tool_calls, tool_waves=s.tool_waves, input_tokens=s.input_tokens, latency_ms=s.latency_ms,
            conversation_id=p.trace.conversation_id if p.trace else None, failure_signature=s.failure,
        ))
    return out


def _category(s: QuestionScore, p: Prediction, found_by: Mapping[str, list[str]], index_of: Mapping[str, str]) -> str | None:
    if p.error is not None:
        return "run_error"
    if s.correct:
        return None
    if s.all_gold_in_context:
        return "reasoning_miss"
    finders = {name for names in found_by.values() for name in names}
    if any(index_of.get(name) == "pipeshub" for name in finders):
        return "retrieval_miss_vs_baseline"
    if finders:
        return "index_miss"
    return "retrieval_miss_all"


def render_diagnostics(target: str, diagnoses: Sequence[QuestionDiagnosis], examples: int = 15) -> str:
    wrong = [d for d in diagnoses if d.category]
    lines = [
        f"## Diagnostics — `{target}`", "",
        f"{len(diagnoses)} answers, {len(wrong)} wrong or failed. Categories use the other systems on the "
        "same questions as controls; see `report/diagnostics.py` for definitions.", "",
        "| Category | Count | Oracle correct |", "|---|---|---|",
    ]
    for category, count in Counter(d.category for d in wrong).most_common():
        oracle_ok = sum(1 for d in wrong if d.category == category and d.oracle_correct)
        lines.append(f"| `{category}` | {count} | {oracle_ok} |")
    faults = Counter(f for d in diagnoses for f in d.faults)
    if faults:
        lines += ["", "| Run fault (all answers) | Count |", "|---|---|"]
        lines += [f"| `{fault}` | {count} |" for fault, count in faults.most_common()]
    for category in ("retrieval_miss_vs_baseline", "index_miss", "reasoning_miss", "run_error"):
        rows = [d for d in wrong if d.category == category][:examples]
        if not rows:
            continue
        lines += ["", f"### `{category}` examples", "", "| q | r | missing gold → found by | faults | conversation |", "|---|---|---|---|---|"]
        for d in rows:
            found = "; ".join(f"{u.rsplit('/', 1)[-1]} → {', '.join(v)}" for u, v in d.found_by.items()) or "–"
            lines.append(f"| {d.question_id} | {d.repeat} | {found} | {', '.join(d.faults) or '–'} | {d.conversation_id or '–'} |")
    return "\n".join(lines) + "\n"


def diagnostics_jsonl(diagnoses: Sequence[QuestionDiagnosis]) -> str:
    return "".join(json.dumps(d.model_dump(mode="json")) + "\n" for d in diagnoses)
