"""S1's task: a CI-migration study carried over five requests in one chat, the way a person works with an agent.

The person asks for a plan, a wiki of notes, cost numbers that need code (the first program fails and is fixed), the
risks, a /compact, a change they then undo, and, after the worker restarted, the final report from the notes and
what was remembered. In between the harness has to survive: two automatic compactions (``usage``), a provider
refusing a request as too large (QAFIX4 B2's journaled path), a sandbox ended between every request, a worker that
dies after a code run's effect and is re-driven, and a new worker process.

Each request's steps are fake-LLM directives (``brain``); code runs go through ``coding_agent``, the delegate a
react-mode agent gets ``run_code`` behind, as a real model's would.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from tests.support.longtask_rig.brain import Step, compile_steps

# Over 0.8 of the window: the ladder compacts before the next request.
WINDOW = 24_000
OVER = 21_000
SYSTEM_MARKER = "You are the golden agent."
CODENAME = "HERON"
CRASH_POINT = "worker.tool.after_effect"
LOG_LINE = "2026-10-20 risks recorded"
REFUSE = "refuse maximum context length exceeded"


def _py(*lines: str) -> str:
    return "\n".join(["import os, pathlib", "W = pathlib.Path(os.environ['WORKSPACE_DIR'])", *lines]) + "\n"


def _write(path: str, text: str) -> str:
    return f"(W / {path!r}).parent.mkdir(parents=True, exist_ok=True); (W / {path!r}).write_text({text!r})"


def _code(goal: str, program: str, *, delegated: bool = True) -> str:
    """A code run: ``run_code`` in the run's own loop, or a ``coding_agent`` call whose goal is the delegate's own
    script (run ``program``, then report ``goal``), as a react-mode model has to."""
    direct = "tool run_code " + json.dumps({"code": program, "language": "python"})
    if not delegated:
        return direct
    return "tool coding_agent " + json.dumps({"goal": f"{direct}\nsay {goal}"})


def todos(*items: tuple[str, str]) -> str:
    return "tool write_todos " + json.dumps({"todos": [{"content": c, "status": s} for c, s in items]})


def remember(content: str, kind: str, scope: str) -> str:
    return "tool remember " + json.dumps({"content": content, "kind": kind, "scope": scope})


PLAN = ("Survey the current CI", "Estimate the costs", "Assess the risks", "Write the recommendation")

INDEX_V1 = (
    "# CI migration study\n\n"
    "- [Sources](sources/overview.md)\n"
    "- [Costs](findings/costs.md)\n"
)
INDEX_V2 = INDEX_V1 + "- [Risks](findings/risks.md)\n- [Decision log](decisions/log.md)\n"
OVERVIEW = "# Sources\n\nJenkins: 14 pipelines, 3 agents. GitHub Actions: hosted runners.\n"
RISKS = "# Risks\n\n1. Self-hosted secrets move.\n2. Matrix builds double minutes.\n3. Plugin parity.\n"
COSTS_HEADER = "# Costs\n\n| Item | USD/month |\n|---|---|\n"
RECOMMENDATION = "# Recommendation (draft)\n\nMigrate in Q1.\n"
REPORT = "# Report\n\nSee [index](index.md). Monthly cost 412 USD; three risks; recommendation pending review.\n"

COST_BUGGY = _py(
    "rows = [('runners', 300), ('storage', 112)]",
    "total = sum(r[1] for r in rows) + rows[2][1]",  # IndexError: the first attempt fails
    "print(total)",
    "pathlib.Path('tmp_costs.csv').write_text('runners,300\\nstorage,112\\n')",
)
COST_FIXED = _py(
    "rows = [('runners', 300), ('storage', 112)]",
    "total = sum(r[1] for r in rows)",
    "table = ''.join(f'| {n} | {v} |\\n' for n, v in rows) + f'| Total | {total} |\\n'",
    _write("wiki/findings/costs.md", COSTS_HEADER) + "; (W / 'wiki/findings/costs.md').open('a').write(table)",
    "print('Monthly cost:', total)",
)
RISK_BUGGY = _py("scores = {'secrets': 3, 'minutes': 2}", "print(scores['plugins'])")
RISK_FIXED = _py("scores = {'secrets': 3, 'minutes': 2, 'plugins': 1}", "print('risk score', sum(scores.values()))")
SCRATCH_OUTSIDE = _py("pathlib.Path('tmp_costs.csv').write_text('runners,300\\nstorage,112\\n')", "print('scratch written')")
READ_SCRATCH = _py("p = pathlib.Path('tmp_costs.csv')", "print('scratch:', p.read_text() if p.exists() else 'MISSING')")


def _cat(*paths: str) -> str:
    return _py(*(f"print(open(W / {p!r}).read())" for p in paths))


LIST_WIKI = _py("print(sorted(str(p.relative_to(W)) for p in W.rglob('*.md')))")
APPEND_LOG = _py(
    "(W / 'wiki/decisions').mkdir(parents=True, exist_ok=True)",
    f"open(W / 'wiki/decisions/log.md', 'a').write({LOG_LINE + chr(10)!r})",
    "print('logged')",
)


@dataclass(frozen=True)
class Request:
    task: str
    text: str
    steps: list[Step]
    compact: str | None = None
    crash: bool = False
    restart_before: bool = False
    undo_after: bool = False
    # A failpoint armed before the request is admitted: the worker dies there (S2's pre-run crash).
    arm_before: str | None = None


@dataclass(frozen=True)
class Variant:
    """How the agent reaches code: ``flat`` (auto mode routed to quick: ``run_code`` and ``write_todos`` in the run's
    own loop) or ``delegated`` (react mode: ``run_code`` only inside ``coding_agent``)."""

    name: str
    chat_mode: str
    route: str | None
    delegated: bool


FLAT = Variant("flat", "auto", "quick", delegated=False)
DELEGATED = Variant("delegated", "react", None, delegated=True)
VARIANTS = (FLAT, DELEGATED)


def requests(variant: Variant = FLAT) -> list[Request]:
    def code(goal: str, program: str) -> str:
        return _code(goal, program, delegated=variant.delegated)

    t1 = [
        todos((PLAN[0], "in_progress"), (PLAN[1], "pending"), (PLAN[2], "pending"), (PLAN[3], "pending")),
        code("Wrote the wiki index and the sources page.", _py(_write("wiki/index.md", INDEX_V1), _write("wiki/sources/overview.md", OVERVIEW), "print('wiki started')")),
        code("Read the index back.", _cat("wiki/index.md")),
        todos((PLAN[0], "completed"), (PLAN[1], "in_progress"), (PLAN[2], "pending"), (PLAN[3], "pending")),
        code("The cost program failed.", COST_BUGGY),
        code("Fixed the cost program; costs are in the wiki.", COST_FIXED),
        code("Kept a scratch copy of the numbers.", SCRATCH_OUTSIDE),
        remember(f"The CI migration study is codenamed {CODENAME}; it compares Jenkins with GitHub Actions.", "fact", "session"),
        remember("The owner wants recommendations as a table.", "preference", "user"),
        f"usage {OVER}\n" + todos((PLAN[0], "completed"), (PLAN[1], "completed"), (PLAN[2], "pending"), (PLAN[3], "pending")),
        code("Checked the costs page after the context was compacted.", _cat("wiki/findings/costs.md")),
        "say The plan has four steps; notes are in wiki/index.md, wiki/sources/overview.md and wiki/findings/costs.md ({result:T1-6}).",
    ]
    t2 = [
        code("Read the costs page in a fresh sandbox.", _cat("wiki/index.md", "wiki/findings/costs.md")),
        code("Looked for the scratch file.", READ_SCRATCH),
        todos((PLAN[0], "completed"), (PLAN[1], "completed"), (PLAN[2], "in_progress"), (PLAN[3], "pending")),
        code("Wrote the risks page and linked it from the index.", _py(_write("wiki/findings/risks.md", RISKS), _write("wiki/index.md", INDEX_V2), "print('risks written')")),
        code("The risk score program failed.", RISK_BUGGY),
        code("Fixed the risk score.", RISK_FIXED),
        f"crash {CRASH_POINT}\n" + code("Logged the decision.", APPEND_LOG),
        code("Read the log.", _cat("wiki/decisions/log.md")),
        f"usage {OVER}\n" + todos((PLAN[0], "completed"), (PLAN[1], "completed"), (PLAN[2], "completed"), (PLAN[3], "pending")),
        code("Checked the risks page after the second compaction.", _cat("wiki/findings/risks.md")),
        code("Listed the wiki.", LIST_WIKI),
        "say Risks are in wiki/findings/risks.md, linked from wiki/index.md; the log is wiki/decisions/log.md.",
    ]
    t3 = [
        code("Read the index after /compact.", _cat("wiki/index.md")),
        code("Read the costs after /compact.", _cat("wiki/findings/costs.md")),
        code("Read the risks after /compact.", _cat("wiki/findings/risks.md")),
        code("Listed the wiki.", LIST_WIKI),
        "say We have a plan, costs (wiki/findings/costs.md) and risks (wiki/findings/risks.md); the recommendation is next.",
    ]
    t4 = [
        code("Read the index before drafting.", _cat("wiki/index.md")),
        todos((PLAN[0], "completed"), (PLAN[1], "completed"), (PLAN[2], "completed"), (PLAN[3], "in_progress")),
        code("Drafted the recommendation.", _py(_write("wiki/decisions/recommendation.md", RECOMMENDATION), "print('drafted')")),
        "say Drafted wiki/decisions/recommendation.md.",
    ]
    t5 = [
        code("Listed the wiki after the undo.", LIST_WIKI),
        code("Read the notes back.", _cat("wiki/index.md", "wiki/findings/costs.md", "wiki/findings/risks.md")),
        f"{REFUSE}\n" + code("Wrote the report.", _py(_write("wiki/report.md", REPORT), "print('report written')")),
        code("Read the report.", _cat("wiki/report.md")),
        code("Read the decision log.", _cat("wiki/decisions/log.md")),
        todos(*((p, "completed") for p in PLAN)),
        "say Final report: wiki/report.md, from wiki/index.md, wiki/findings/costs.md and wiki/findings/risks.md. "
        "Remembered: {recalled}",
    ]
    return [
        Request("T1", f"[T1] Start the CI migration study ({CODENAME}): plan it, keep notes in a wiki, estimate the costs.", compile_steps("T1", t1)),
        Request("T2", "[T2] Continue the study: assess the migration risks and keep the wiki current.", compile_steps("T2", t2), crash=True),
        Request("T3", "[T3] Where are we?", compile_steps("T3", t3), compact="keep the cost numbers and the wiki pages"),
        Request("T4", "[T4] Draft the recommendation page.", compile_steps("T4", t4), undo_after=True),
        Request("T5", "[T5] Finish the report from the notes and what you remember.", compile_steps("T5", t5), restart_before=True),
    ]


code = _code

__all__ = [
    "CODENAME", "CRASH_POINT", "DELEGATED", "FLAT", "LOG_LINE", "OVER", "PLAN", "SYSTEM_MARKER", "VARIANTS", "WINDOW",
    "Request", "Variant", "code", "requests",
]
