"""Capacity model: what a recorded run would cost the durable journal (P0.9).

    python -m tests.harness_replay.capacity                 # print the table
    python -m tests.harness_replay.capacity --write         # refresh the doc
    python -m tests.harness_replay.capacity --check         # exit 1 if stale

Each replay-corpus recording is projected onto the P1 journal under the commit
rules of REPORT §6.1.4: which events it writes, how they group into commits,
how many bytes stay inline and how many are claim-checked to CAS, and, with
an assumed model and tool latency, the commit rate a worker sees at N
concurrent runs. The committed output is ``docs/agent-harness-v2/capacity-
baseline.md``; P1's load test (TEST-STRATEGY §8.2) replaces the assumed
latencies with measured ones.

The model is deliberately conservative: every write intent is its own commit
(a parallel batch of writes could share one), and a model error retried
in-process adds no commit.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

_BACKEND_PYTHON = Path(__file__).resolve().parents[2]
if str(_BACKEND_PYTHON) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PYTHON))

from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS  # noqa: E402
from harness_core.testing.replay import (  # noqa: E402
    Recording,
    ToolStep,
)
from tests.harness_replay.record import CORPUS_DIR  # noqa: E402
from tests.support.parity_cases.harness_graders import SANDBOX_EXEC_TOOLS, WRITE_TOOLS  # noqa: E402

DOC_PATH = (
    _BACKEND_PYTHON.parents[1] / "docs" / "agent-harness-v2" / "capacity-baseline.md"
)

# REPORT §6.1.2: payloads above this are claim-checked to CAS.
INLINE_LIMIT_BYTES = 256 * 1024
# The envelope without `data`: org/session/stream/run/attempt ids, seq, fence,
# type, schemaVersion, critical, ts, writer, labels, visibility: 399 bytes as
# compact JSON with 24-char ids, rounded up for `_id` and BSON framing.
ENVELOPE_BYTES = 420
PAYLOAD_REF_BYTES = 120
# model.response journals the provider's raw state beside the message
# (robustness #18); assume it is as large as the normalized response.
RAW_PROVIDER_COPIES = 1
# Per commit, besides its events: command receipt, stream seq counter, run
# document CAS and one outbox row.
DOCS_PER_COMMIT = 4

# Assumed until P1 measures them: one model call, one tool batch.
MODEL_CALL_S = 3.0
TOOL_BATCH_S = 0.5
CONCURRENCY = (1, 4, 16, 64)
# Measured on the PERF-C rig (PERFC-F1): what the corpus turn costs the single thread
# of one Node process, and the commit rate at which that thread is saturated.
NODE_CPU_MS_PER_COMMIT = 4.0
NODE_CPU_MS_PER_ADMISSION = 9.0
NODE_CPU_MS_PER_ENDING_COMMIT = 9.0
NODE_SATURATED_COMMITS_PER_S = 62.0

# Code mode (robustness #3, #23): gateway calls inside one run_code, with read
# outcomes batched this many per commit: one event an outcome, as many as a
# commit holds (the contract's journal.maxCommitEvents).
CODE_MODE_CALLS = 5000
CODE_MODE_RESULT_BYTES = 2048
CODE_MODE_OUTCOME_BATCH = MAX_COMMIT_EVENTS
CODE_MODE_CALLS_PER_S = 50.0

EffectClass = Literal["read", "write", "execute", "interaction"]
INTERACTION_TOOLS = frozenset({"internaltools__ask_user_question"})

Writer = Literal["node", "worker"]


def effect_class(tool: str) -> EffectClass:
    if tool in WRITE_TOOLS:
        return "write"
    if tool in SANDBOX_EXEC_TOOLS:
        return "execute"
    if tool in INTERACTION_TOOLS:
        return "interaction"
    return "read"


def _size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


@dataclass(frozen=True)
class Event:
    type: str
    payloads: tuple[int, ...] = ()

    @property
    def inline_bytes(self) -> int:
        return ENVELOPE_BYTES + sum(
            p if p <= INLINE_LIMIT_BYTES else PAYLOAD_REF_BYTES for p in self.payloads
        )

    @property
    def blob_bytes(self) -> int:
        return sum(p for p in self.payloads if p > INLINE_LIMIT_BYTES)


@dataclass
class Commit:
    kind: str
    writer: Writer = "worker"
    events: list[Event] = field(default_factory=list[Event])

    @property
    def types(self) -> list[str]:
        return [e.type for e in self.events]


@dataclass
class Projection:
    name: str
    mode: str
    commits: list[Commit]
    model_calls: int
    tool_calls: int
    tool_batches: int
    turn_commits: list[tuple[int, int]]
    """(tools in the turn, commits the turn made) for every tool-using turn."""
    wall_s: float | None = None
    """Overrides the model/tool latency estimate (code mode is script-bound)."""

    @property
    def events(self) -> int:
        return sum(len(c.events) for c in self.commits)

    @property
    def inline_bytes(self) -> int:
        return sum(e.inline_bytes for c in self.commits for e in c.events)

    @property
    def blob_bytes(self) -> int:
        return sum(e.blob_bytes for c in self.commits for e in c.events)

    @property
    def mongo_docs(self) -> int:
        return self.events + DOCS_PER_COMMIT * len(self.commits)

    @property
    def write_intents(self) -> int:
        return sum(c.kind == "intent" for c in self.commits)

    @property
    def duration_s(self) -> float:
        if self.wall_s is not None:
            return self.wall_s
        return self.model_calls * MODEL_CALL_S + self.tool_batches * TOOL_BATCH_S

    def commits_by(self, writer: Writer) -> int:
        return sum(c.writer == writer for c in self.commits)


def project(recording: Recording, name: str | None = None) -> Projection:
    """Project one recorded run onto journal commits (REPORT §6.1.4)."""
    commits = [
        Commit(
            "admit",
            "node",
            [
                Event("run.admitted"),
                Event("input.queued", (_size(recording.inputs.query),)),
            ],
        ),
        # Claim and spec pin are one txn. The spec's size is dominated by the grants.
        Commit(
            "claim",
            "node",
            [
                Event("run.claimed"),
                Event("run.spec_pinned", (_size(_first_tools(recording)),)),
            ],
        ),
    ]
    steps = recording.steps
    model_calls = tool_calls = batches = 0
    turn_commits: list[tuple[int, int]] = []
    carried: list[Event] = []
    i = 0
    while i < len(steps):
        step = steps[i]
        i += 1
        if isinstance(step, ToolStep):
            # A tool step with no model step before it; count it, journal it alone.
            tool_calls += 1
            commits.append(Commit("outcomes", events=[_outcome(step)]))
            continue
        model_calls += 1
        if step.response is None:
            continue
        resp = step.response
        payload = _size(resp.model_dump(mode="json"))
        model_commit = Commit(
            "model.response",
            events=[
                *carried,
                Event("turn.started"),
                Event("model.request"),
                Event("model.response", (payload,) * (1 + RAW_PROVIDER_COPIES)),
                Event("usage.recorded"),
            ],
        )
        carried = []
        commits.append(model_commit)
        batch: list[ToolStep] = []
        while i < len(steps) and isinstance(steps[i], ToolStep):
            batch.append(steps[i])  # type: ignore[arg-type]
            i += 1
        if not resp.tool_calls:
            model_commit.events.append(Event("message.completed", (_size(resp.text),)))
            model_commit.events.append(Event("turn.completed"))
            if i >= len(steps):
                model_commit.events.append(_terminal(recording))
            continue

        before = len(commits) - 1
        batches += 1
        tool_calls += len(batch)
        outcomes: list[list[Event]] = []
        for call in batch:
            cls = effect_class(call.name)
            if cls in ("write", "execute"):
                commits.append(
                    Commit("intent", events=[Event("tool.intent", (_size(call.args),))])
                )
                outcomes.append([_outcome(call)])
            elif cls == "interaction":
                commits.append(
                    Commit(
                        "suspend",
                        events=[
                            Event("interaction.requested", (_size(call.args),)),
                            Event("run.suspended"),
                        ],
                    )
                )
                if i < len(steps):
                    commits.append(
                        Commit(
                            "resolve",
                            "node",
                            [
                                Event("interaction.resolved", (_size(call.result),)),
                                Event("run.resumed"),
                            ],
                        )
                    )
                    commits.append(Commit("claim", "node", [Event("run.claimed")]))
            else:
                outcomes.append([Event("tool.intent", (_size(call.args),)), _outcome(call)])
        if outcomes:
            parts, carried = outcome_commits(outcomes)
            commits.extend(parts)
        turn_commits.append(
            (len(batch), sum(c.writer == "worker" for c in commits[before:]))
        )
    ended = bool(commits[-1].types) and commits[-1].types[-1] in _TERMINAL
    if not ended and recording.final.status != "needs_input":
        commits.append(Commit("terminal", events=[*carried, _terminal(recording)]))
    return Projection(
        name=name or recording.manifest.scenario,
        mode=recording.inputs.chat_mode,
        commits=commits,
        model_calls=model_calls,
        tool_calls=tool_calls,
        tool_batches=batches,
        turn_commits=turn_commits,
    )


def outcome_commits(
    units: list[list[Event]], limit: int = MAX_COMMIT_EVENTS
) -> tuple[list[Commit], list[Event]]:
    """A batch's outcomes as the kernel commits them: a call's events stay in one commit, and a batch
    with more events than a commit holds (``journal.maxCommitEvents``) is consecutive commits. The
    turn's end rides with the last of them; when that one is full, with the run's next commit
    (returned)."""
    commits = [Commit("outcomes")]
    for unit in units:
        if commits[-1].events and len(commits[-1].events) + len(unit) > limit:
            commits.append(Commit("outcomes"))
        commits[-1].events.extend(unit)
    end = Event("turn.completed")
    if len(commits[-1].events) < limit:
        commits[-1].events.append(end)
        return commits, []
    return commits, [end]


_TERMINAL = frozenset({"run.completed", "run.failed", "run.suspended"})


def _first_tools(recording: Recording) -> list[str]:
    first = next(iter(recording.model_steps()), None)
    return list(first.request.tools) if first else []


def _outcome(call: ToolStep) -> Event:
    if call.status == "denied":
        return Event("tool.denied", (_size(call.error),))
    return Event("tool.outcome", (_size(call.result) + _size(call.sources),))


def _terminal(recording: Recording) -> Event:
    if recording.final.status == "completed":
        return Event("run.completed", (_size(recording.final.answer),))
    return Event("run.failed")


def project_code_mode(
    calls: int = CODE_MODE_CALLS,
    result_bytes: int = CODE_MODE_RESULT_BYTES,
    batch: int = CODE_MODE_OUTCOME_BATCH,
) -> Projection:
    """A code-mode run: one run_code whose script makes ``calls`` gateway reads."""
    commits = [
        Commit("admit", "node", [Event("run.admitted"), Event("input.queued", (200,))]),
        Commit(
            "claim", "node", [Event("run.claimed"), Event("run.spec_pinned", (2000,))]
        ),
        Commit(
            "model.response",
            events=[
                Event("turn.started"),
                Event("model.request"),
                Event("model.response", (4000,) * (1 + RAW_PROVIDER_COPIES)),
                Event("usage.recorded"),
            ],
        ),
        Commit("intent", events=[Event("tool.intent", (4000,))]),
    ]
    for start in range(0, calls, batch):
        n = min(batch, calls - start)
        commits.append(
            Commit(
                "outcomes",
                events=[Event("tool.outcome", (result_bytes,)) for _ in range(n)],
            )
        )
    commits.append(
        Commit(
            "outcomes", events=[Event("tool.outcome", (8000,)), Event("turn.completed")]
        )
    )
    commits.append(
        Commit(
            "model.response",
            events=[
                Event("turn.started"),
                Event("model.request"),
                Event("model.response", (2000,) * (1 + RAW_PROVIDER_COPIES)),
                Event("usage.recorded"),
                Event("message.completed", (1500,)),
                Event("turn.completed"),
                Event("run.completed", (1500,)),
            ],
        )
    )
    return Projection(
        name=f"synthetic: code mode, {calls:,} calls",
        mode="code",
        commits=commits,
        model_calls=2,
        tool_calls=1,
        tool_batches=1,
        turn_commits=[],
        wall_s=2 * MODEL_CALL_S + calls / CODE_MODE_CALLS_PER_S,
    )


def project_loop_reads(turns: int, calls: int, result_bytes: int = CODE_MODE_RESULT_BYTES) -> Projection:
    """A loop run of ``turns`` model turns of ``calls`` parallel reads each, then an answer (QA-22's
    ``code-mode-5k`` sessions), by the same rules ``project`` applies to a recorded read batch."""
    commits = [
        Commit("admit", "node", [Event("run.admitted"), Event("input.queued", (200,))]),
        Commit("claim", "node", [Event("run.claimed"), Event("run.spec_pinned", (2000,))]),
    ]
    request = [{"id": f"call_{i}", "name": "read", "args": {"i": i}} for i in range(calls)]
    carried: list[Event] = []
    batch_commits = 0
    for _ in range(turns):
        commits.append(Commit("model.response", events=[
            *carried, Event("turn.started"), Event("model.request"),
            Event("model.response", (_size(request),) * (1 + RAW_PROVIDER_COPIES)), Event("usage.recorded"),
        ]))
        reads = [
            [Event("tool.intent", (_size({"i": i}),)), Event("tool.outcome", (_size({"i": i, "blob": "r" * result_bytes}),))]
            for i in range(calls)
        ]
        parts, carried = outcome_commits(reads)
        batch_commits = len(parts)
        commits.extend(parts)
    commits.append(Commit("model.response", events=[
        *carried, Event("turn.started"), Event("model.request"), Event("model.response", (2000,) * (1 + RAW_PROVIDER_COPIES)),
        Event("usage.recorded"), Event("message.completed", (1500,)), Event("turn.completed"),
        Event("run.completed", (1500,)),
    ]))
    return Projection(
        name=f"synthetic: loop, {turns} x {calls} reads", mode="react", commits=commits, model_calls=turns + 1,
        tool_calls=turns * calls, tool_batches=turns, turn_commits=[(calls, 1 + batch_commits)] * turns,
    )


def load_corpus(directory: Path = CORPUS_DIR) -> list[Projection]:
    return [
        project(Recording.load(path), path.stem)
        for path in sorted(directory.glob("*.json"))
    ]


def slo_turn_bound(tools: int) -> int:
    """TEST-STRATEGY §8.3: Mongo commits per tool-using turn ≤ 2 + 2 × tools."""
    return 2 + 2 * tools


def _kb(n: float) -> str:
    return f"{n / 1024:,.1f}"


def render(corpus: list[Projection], synthetic: list[Projection]) -> str:
    rows = [*corpus, *synthetic]
    out = [
        "<!-- Generated by `python -m tests.harness_replay.capacity --write`"
        " (backend/python); edit the model, not this file. -->\n\n",
        "# Capacity baseline (P0.9)\n\n",
        "Every replay-corpus recording (`backend/python/tests/fixtures/journals/legacy/`)"
        " projected onto the P1 journal under the commit rules of REPORT §6.1.4. "
        "Model: `backend/python/tests/harness_replay/capacity.py`. See `SLO.md` for the"
        " gates these numbers feed.\n\n",
        "## Assumptions\n\n",
        "| Constant | Value | Source |\n| --- | --- | --- |\n",
        f"| Inline payload limit | {INLINE_LIMIT_BYTES // 1024} KB | REPORT §6.1.2 claim-check |\n",
        f"| Envelope bytes per event | {ENVELOPE_BYTES} | §6.1.2 envelope, 24-char ids |\n",
        f"| Payload ref bytes | {PAYLOAD_REF_BYTES} | `{{ref, hash, size}}` |\n",
        f"| Raw provider copies per `model.response` | {RAW_PROVIDER_COPIES} | robustness #18 |\n",
        f"| Extra Mongo docs per commit | {DOCS_PER_COMMIT} | receipt, seq counter, run CAS, outbox |\n",
        f"| Model call latency | {MODEL_CALL_S} s | assumed until the P1 load test |\n",
        f"| Tool batch latency | {TOOL_BATCH_S} s | assumed until the P1 load test |\n",
        f"| Code-mode gateway calls/s | {CODE_MODE_CALLS_PER_S:g} | assumed |\n",
        f"| Code-mode read outcomes per commit | {CODE_MODE_OUTCOME_BATCH} | robustness #23 |\n\n",
        "Commit rules applied: admission (Node) and claim + spec pin (Node) are one commit"
        " each; `model.response` (with `model.request`, `usage.recorded`, `turn.started`) is"
        " its own commit before any dispatch; each `write`/`execute` intent is its own"
        " commit; `read` intents ride with their outcomes; all outcomes of a batch are one"
        f" commit, or consecutive commits past {MAX_COMMIT_EVENTS} events (the journal's limit for one"
        " commit; a call's events stay together); a final answer carries `message.completed` and `run.completed` in its"
        " model commit; an interaction is a suspension commit (plus Node resolve and"
        " re-claim when the run continues).\n\n",
        "## Per run (one user turn)\n\n",
        "| Recording | Mode | Model calls | Tool calls | Write intents | Commits (worker + node)"
        " | Events | Mongo docs | Inline KB | CAS KB | Max commits / tool turn (SLO bound) |\n",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |\n",
    ]
    for p in rows:
        worst = max(
            p.turn_commits, key=lambda t: t[1] - slo_turn_bound(t[0]), default=None
        )
        bound = f"{worst[1]} (≤ {slo_turn_bound(worst[0])})" if worst else "—"
        out.append(
            f"| `{p.name}` | {p.mode} | {p.model_calls} | {p.tool_calls:,} | "
            f"{p.write_intents} | {len(p.commits)} ({p.commits_by('worker')} + "
            f"{p.commits_by('node')}) | {p.events:,} | {p.mongo_docs:,} | "
            f"{_kb(p.inline_bytes)} | {_kb(p.blob_bytes)} | {bound} |\n"
        )
    n = len(corpus)
    mean_commits = sum(len(p.commits) for p in corpus) / n
    mean_bytes = sum(p.inline_bytes for p in corpus) / n
    mean_duration = sum(p.duration_s for p in corpus) / n
    out.append(
        f"\nCorpus mean: {mean_commits:.1f} commits, {_kb(mean_bytes)} KB inline"
        f" journal per run, {mean_duration:.1f} s assumed wall time.\n\n"
    )

    out.append("## Commit and journal rate per worker at N concurrent runs\n\n")
    out.append(
        "Commits/s = N × commits ÷ assumed run duration; journal growth = N × inline"
        " bytes ÷ duration. A worker at N runs in steady state.\n\n"
    )
    head = " | ".join(f"N={c}" for c in CONCURRENCY)
    out.append(
        f"| Profile | Metric | {head} |\n| --- | --- | {' | '.join('---:' for _ in CONCURRENCY)} |\n"
    )
    profiles: list[tuple[str, float, float, float]] = [
        ("corpus mean", mean_commits, mean_bytes, mean_duration),
    ]
    busiest = max(corpus, key=lambda p: len(p.commits) / p.duration_s)
    profiles.append(
        (
            f"busiest recording (`{busiest.name}`)",
            len(busiest.commits),
            busiest.inline_bytes,
            busiest.duration_s,
        )
    )
    profiles.extend(
        (s.name, len(s.commits), s.inline_bytes, s.duration_s) for s in synthetic
    )
    for label, commits, inline, dur in profiles:
        cps = " | ".join(f"{c * commits / dur:,.1f}" for c in CONCURRENCY)
        mbh = " | ".join(
            f"{c * inline / dur * 3600 / 1024**2:,.1f}" for c in CONCURRENCY
        )
        out.append(f"| {label} | commits/s | {cps} |\n")
        out.append(f"| | journal MB/hour | {mbh} |\n")
    out.append(
        "\nThe per-session seq counter serializes commits within one session only"
        " (robustness #3); these rates are spread across N sessions. A single code-mode"
        " session commits at the rate in its N=1 column on one counter.\n"
    )

    out.append("\n## One Node process (measured)\n\n")
    out.append(
        "Node serves every admission, claim and commit on one thread. Measured on the PERF-C rig"
        " (PERFC-F1; MongoDB itself answers in a millisecond or two):"
        f" a commit costs that thread about {NODE_CPU_MS_PER_COMMIT:.0f} ms, an admission about"
        f" {NODE_CPU_MS_PER_ADMISSION:.0f} ms and the commit that ends a run about"
        f" {NODE_CPU_MS_PER_ENDING_COMMIT:.0f} ms; with the claims, streams and logs of the same turns the"
        f" thread is saturated at about {NODE_SATURATED_COMMITS_PER_S:.0f} commits/s of the corpus turn."
        " Requests that arrive in the same instant queue on it: k commits at once end about"
        f" k × {NODE_CPU_MS_PER_COMMIT:.0f} ms later, so SLO #1's 100 ms leaves room for about"
        f" {int(100 // NODE_CPU_MS_PER_COMMIT)} commits of one instant on one process, or"
        f" {int(100 // NODE_CPU_MS_PER_ENDING_COMMIT)} ending commits.\n\n"
        "These per-request figures are PERF-C's derived ones; PERF-D and PERF-E measured them about twice as high"
        " on a loaded host (PERFD-F2), and stage 12"
        " sets them from a quiet host.\n\n"
        "**What is held per process and what is counted here (PERFD-F1, decided 2026-10-18).** Compose ships one"
        " Node process; Helm one per pod, scaled by its autoscaler on one core of CPU or on Node's event-loop"
        " utilization. SLO #1 and SLO #4 for sessions in step are held on one process (`commits-n16-instep`)"
        " whatever the topology: the rows below count how many processes a *rate* needs, which is a sizing"
        " figure, never a reason to restate a latency gate.\n\n"
    )
    out.append(
        f"| Profile | Metric | {head} |\n| --- | --- | {' | '.join('---:' for _ in CONCURRENCY)} |\n"
    )
    for label, commits, _inline, dur in profiles:
        share = " | ".join(
            f"{100 * c * commits / dur / NODE_SATURATED_COMMITS_PER_S:,.0f}%" for c in CONCURRENCY
        )
        processes = " | ".join(
            str(max(1, math.ceil(c * commits / dur / NODE_SATURATED_COMMITS_PER_S))) for c in CONCURRENCY
        )
        out.append(f"| {label} | share of one Node process | {share} |\n")
        out.append(f"| | Node processes at saturation | {processes} |\n")
    out.append(
        "\nSize for headroom, not for saturation: at the saturated rate every request waits behind the"
        " others (the overload profile `commits-n16-burst` measures that queue).\n"
    )
    return "".join(out)


def build() -> str:
    return render(load_corpus(), [project_code_mode()])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help=f"write {DOC_PATH.name}")
    group.add_argument(
        "--check", action="store_true", help="exit 1 if the doc is stale"
    )
    args = parser.parse_args(argv)
    text = build()
    if args.write:
        DOC_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {DOC_PATH}")
        return 0
    if args.check:
        current = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else ""
        if current != text:
            print(
                f"{DOC_PATH} is stale: run `python -m tests.harness_replay.capacity --write`"
            )
            return 1
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
