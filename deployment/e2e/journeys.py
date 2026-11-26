"""R31-JOURNEY (ADR-TEST-01): the feature registry's journeys on a human-style stack, on the scripted model.

    python3 journeys.py <run dir> [--tier gate|full] [--only J-…[,J-…]] [--parallel N] [--out FILE]

The plan is docs/test-plan/generated/journey-plan.json (scripts/journey_matrix.py --write): one journey per (path,
shape, turn) with the features that turn proves on that path. A journey is one or more conversations driven the way the
web app drives them (smoke_human.py's client), then read back: what the model was offered and called, what the fake
SaaS received, what the person's stream and the saved conversation hold, what the run pinned. Each feature's line is
proved by behaviour (a tool result the person can see, a card that held a call, an effect that happened once or never),
never by a status alone.

The account shapes come from deployment/e2e/owner_shape.json (derived read-only from a large real-world org's
MongoDB by scripts/owner_shape.py): the seeded shape is seed_human.py's; the owner shapes add the person's second MCP
server held whole and set the org's or the person's coding engine for the journey's group, put back after it.

Prints one line per feature per journey, writes a JSON report (``--out``, default <run dir>/journeys.json) and exits
non-zero when any line failed. The literals SUPPORT, DRIVEN and TURNS are what the coverage check reads: the runner
refuses to start when they and its probes disagree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLAN = ROOT / "docs/test-plan/generated/journey-plan.json"
SHAPE = HERE / "owner_shape.json"
Json = dict[str, Any]

# --- what this runner runs (read by scripts/journey_matrix.py; held to the probes below at start) --------------------

LOOP_PATHS = ("assistant.agent", "agent.quick", "agent.deep", "loop.subagent")
# R35SLACK-FU-1: the built-in assistant's chat modes on the harness (internal_search, web_search), the web app's mode menu.
CHAT_MODE_PATH = "assistant.mode"
JOB_PATHS = ("job.opencode", "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose",
             "job.acp-qwen", "job.acp-dsh")
SUPPORT: dict[str, tuple[str, ...]] = {
    "mcp.named": ("classic", "agent.quick", "agent.deep", "loop.subagent", "job.opencode", "job.codex",
                  "job.claude_code", "resume.approval", "resume.question", "scheduled.cron", "scheduled.webhook",
                  "surface.mcp", "recovery", "surface.slack"),
    "mcp.first_use": ("assistant.agent", "job.opencode", "job.codex", "job.claude_code", "job.acp-opencode",
                      "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh", "resume.approval",
                      "resume.question", "recovery", "surface.slack"),
    "skills.preload": ("assistant.agent", "agent.quick", "agent.deep", "job.opencode", "job.codex", "job.claude_code",
                       "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh",
                       "resume.approval", "resume.question", "scheduled.cron", "scheduled.webhook", "surface.mcp",
                       "recovery", "loop.subagent", "surface.slack"),
    "skills.load": ("assistant.agent", "agent.quick", "loop.subagent", "job.opencode", "job.codex", "job.claude_code",
                    "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh",
                    "scheduled.cron", "scheduled.webhook", "surface.mcp", "agent.deep", "classic", "surface.slack",
                    "assistant.mode"),
    "knowledge": ("classic", "assistant.agent", "agent.quick", "job.opencode", "job.codex", "job.claude_code",
                  "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh",
                  "scheduled.cron", "scheduled.webhook", "surface.mcp", "surface.slack", "agent.deep", "loop.subagent",
                  "assistant.mode"),
    "artifacts": ("job.opencode", "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini",
                  "job.acp-goose", "job.acp-qwen", "job.acp-dsh", "command", "classic", "assistant.agent",
                  "agent.quick", "agent.deep", "loop.subagent", "resume.approval", "resume.question",
                  "scheduled.cron", "scheduled.webhook", "surface.mcp", "surface.slack", "recovery", "assistant.mode"),
    "approval.ask": ("assistant.agent", "agent.quick", "loop.subagent", "job.opencode", "job.codex",
                     "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
                     "job.acp-dsh", "resume.approval", "resume.question", "scheduled.cron", "scheduled.webhook",
                     "surface.mcp", "recovery", "agent.deep", "surface.slack"),
    "approval.block": ("classic", "assistant.agent", "agent.quick", "loop.subagent", "job.opencode", "job.codex",
                       "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
                       "job.acp-dsh", "resume.approval", "resume.question", "scheduled.cron", "scheduled.webhook",
                       "surface.mcp", "recovery", "agent.deep", "surface.slack"),
    "approval.org_rule": ("classic", "assistant.agent", "agent.quick", "loop.subagent", "job.opencode", "job.codex",
                          "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
                          "job.acp-dsh", "scheduled.cron", "scheduled.webhook", "surface.mcp", "agent.deep",
                          "surface.slack"),
    "approval.allow": ("assistant.agent", "agent.quick", "loop.subagent", "job.opencode", "job.codex",
                       "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
                       "job.acp-dsh", "resume.approval", "resume.question", "scheduled.cron", "scheduled.webhook",
                       "recovery", "agent.deep", "surface.slack"),
    "question": ("assistant.agent", "agent.quick", "resume.question", "resume.approval", "scheduled.cron",
                 "scheduled.webhook", "surface.mcp", "recovery", "job.opencode", "job.codex", "job.claude_code",
                 "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh", "agent.deep",
                 "assistant.mode", "surface.slack"),
    "cancel": ("classic", "assistant.agent", "agent.quick", "agent.deep", "job.opencode", "job.codex",
               "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
               "job.acp-dsh", "command", "resume.approval", "resume.question", "assistant.mode", "surface.mcp",
               "surface.slack", "recovery"),
    "delete": ("classic", "assistant.agent", "agent.quick", "agent.deep", "job.opencode", "job.codex",
               "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
               "job.acp-dsh", "command", "assistant.mode", "surface.mcp", "surface.slack"),
    "mcp.carried_pins": ("job.opencode", "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini",
                         "job.acp-goose", "job.acp-qwen", "job.acp-dsh"),
    "budget_partial": ("job.opencode", "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini",
                       "job.acp-goose", "job.acp-qwen", "job.acp-dsh", "assistant.agent", "agent.quick", "agent.deep",
                       "loop.subagent", "resume.approval", "resume.question", "scheduled.cron", "scheduled.webhook", "surface.mcp", "surface.slack", "recovery",
                       "assistant.mode"),
    "sandbox.code": ("agent.deep", "command", "assistant.agent", "agent.quick", "loop.subagent", "resume.approval",
                     "resume.question", "scheduled.cron", "scheduled.webhook", "surface.mcp", "surface.slack", "recovery"),
    "sandbox.memory": ("assistant.agent", "agent.quick", "job.opencode", "job.codex", "job.claude_code",
                       "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh",
                       "command", "resume.approval", "resume.question", "classic", "loop.subagent",
                       "scheduled.cron", "scheduled.webhook", "surface.mcp", "surface.slack", "recovery"),
    "repository.fetch": ("job.opencode", "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini",
                         "job.acp-goose", "job.acp-qwen", "job.acp-dsh"),
    "repository.denied": ("job.opencode", "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini",
                          "job.acp-goose", "job.acp-qwen", "job.acp-dsh"),
    "repository.named": ("job.opencode", "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini",
                         "job.acp-goose", "job.acp-qwen", "job.acp-dsh"),
    "repository.not_cloned": ("job.opencode", "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini",
                              "job.acp-goose", "job.acp-qwen", "job.acp-dsh"),
    "web": ("classic", "assistant.agent", "agent.quick", "agent.deep", "loop.subagent", "job.opencode", "job.codex",
            "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh",
            "scheduled.cron", "scheduled.webhook", "surface.mcp", "assistant.mode", "surface.slack"),
    "approval.flow_guard": ("assistant.agent", "agent.quick", "agent.deep", "loop.subagent", "job.opencode",
                            "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose",
                            "job.acp-qwen", "job.acp-dsh", "scheduled.cron", "scheduled.webhook", "surface.mcp",
                            "classic", "surface.slack"),
    "attachments": ("classic", "assistant.agent", "agent.quick", "agent.deep", "job.opencode", "job.codex",
                    "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
                    "job.acp-dsh", "assistant.mode"),
    "toolset": ("classic", "assistant.agent", "agent.quick", "agent.deep", "loop.subagent", "job.opencode",
                "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
                "job.acp-dsh", "scheduled.cron", "scheduled.webhook", "surface.mcp", "surface.slack"),
    # R49-PLAN (ADR-OWD-07 D-PLAN-1): a root loop turn shows its plan; the search modes are never offered it.
    "plan": ("assistant.agent", "agent.quick"),
    # R49-PLAN (ADR-MEM-01 D7): a memory kept for one conversation, on the loop paths whose runs the person starts.
    "memory.session": ("assistant.agent", "agent.quick", "assistant.mode"),
    "memory": ("assistant.agent", "agent.quick", "agent.deep", "job.opencode", "job.codex", "job.claude_code",
               "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh", "surface.mcp",
               "surface.slack", "assistant.mode"),
    # R31JOURNEY-FU-5: a subject nobody's knowledge holds, searched by the job a turn hands off; the loop paths run no
    # grounding check (ADR-RUN-03, X-CONTRACT-LOOP).
    "answer_contract": ("job.opencode", "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini",
                        "job.acp-goose", "job.acp-qwen", "job.acp-dsh"),
    "erasure": ("classic", "assistant.agent", "agent.quick", "job.opencode", "scheduled.cron", "command"),
    # R36COVER-FU-2: a deep run's coordinator, a default chat whose job is running, a second /mcp call.
    "steering": ("assistant.agent", "agent.quick", "assistant.mode", "surface.slack", "agent.deep", "job.opencode",
                 "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
                 "job.acp-dsh", "surface.mcp"),
    "mcp.hub": ("classic", "assistant.agent", "agent.quick", "agent.deep", "loop.subagent", "job.opencode", "job.codex",
                "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh",
                "scheduled.cron", "scheduled.webhook", "surface.mcp", "surface.slack"),
    "mcp.oauth": ("classic", "assistant.agent", "agent.quick", "agent.deep", "loop.subagent", "job.opencode",
                  "job.codex", "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen",
                  "job.acp-dsh", "scheduled.cron", "scheduled.webhook", "surface.mcp", "surface.slack"),
    "approval.decline_carried": ("assistant.agent", "agent.quick", "agent.deep", "job.opencode", "job.codex",
                                 "job.claude_code", "job.acp-opencode", "job.acp-gemini", "job.acp-goose",
                                 "job.acp-qwen", "job.acp-dsh"),
    "approval.person_scoped": ("assistant.agent",),
    # R43MCP-FU-1: the default chat's jobs, its resumed, recovered and Slack runs (the search modes hold no MCP server).
    "mcp.find_read": ("assistant.agent", "job.opencode", "job.codex", "job.claude_code",
                      "job.acp-opencode", "job.acp-gemini", "job.acp-goose", "job.acp-qwen", "job.acp-dsh",
                      "resume.approval", "resume.question", "recovery", "surface.slack"),
}
DRIVEN: dict[str, tuple[str, ...]] = {
    "classic": ("seeded",),
    "assistant.agent": ("owner", "owner_pinned"),
    "agent.quick": ("seeded", "service"),
    "agent.deep": ("seeded",),
    "loop.subagent": ("seeded",),
    "job.opencode": ("owner", "owner_pinned", "seeded"),
    "job.codex": ("owner", "owner_pinned", "seeded"),
    "job.claude_code": ("owner", "owner_pinned", "seeded"),
    "job.acp-opencode": ("owner", "owner_pinned"),
    "job.acp-gemini": ("owner", "owner_pinned"),
    "job.acp-goose": ("owner", "owner_pinned"),
    "job.acp-qwen": ("owner", "owner_pinned"),
    "job.acp-dsh": ("owner", "owner_pinned"),
    "resume.approval": ("seeded", "owner"),
    "resume.question": ("seeded", "owner", "owner_pinned"),
    "scheduled.cron": ("seeded", "service"),
    "scheduled.webhook": ("seeded", "service"),
    "surface.mcp": ("seeded",),
    "surface.slack": ("seeded", "owner"),
    "recovery": ("seeded", "owner"),
    "command": ("owner",),
    "assistant.mode": ("owner",),
}
# Cells the runner leaves out on a path and shape it otherwise drives (the coverage check reads it): each is a gap.
SKIP: dict[str, tuple[str, ...]] = {}
# The features one conversation proves together; a held call, a question, a cancel, a delete and a budget cut each end
# their turn their own way, so each has its own.
TURNS: dict[str, tuple[str, ...]] = {
    "use": ("mcp.named", "mcp.first_use", "skills.preload", "skills.load", "knowledge", "artifacts"),
    "deny": ("approval.ask", "approval.block", "approval.org_rule"),
    "allow": ("approval.allow",),
    "question": ("question",),
    "cancel": ("cancel",),
    "delete": ("delete",),
    "budget": ("budget_partial",),
    "pins": ("mcp.carried_pins",),
    "sandbox": ("sandbox.code",),
    "repo_allow": ("repository.fetch",),
    "repo_deny": ("repository.denied",),
    "repo_named": ("repository.named",),
    "repo_unread": ("repository.not_cloned",),
    "oom": ("sandbox.memory",),
    "web": ("web",),
    "flow": ("approval.flow_guard",),
    "attach": ("attachments",),
    "toolset": ("toolset",),
    "memory": ("memory",),
    "memory_session": ("memory.session",),
    "plan": ("plan",),
    "steer": ("steering",),
    "erase": ("erasure",),
    "contract": ("answer_contract",),
    "hub": ("mcp.hub", "mcp.oauth"),
    "carry": ("approval.decline_carried",),
    "shared": ("approval.person_scoped",),
    "find_read": ("mcp.find_read",),
}
# A turn that counts what a server was asked runs alone in its group: every journey lists the same scripted SaaS. The
# sandbox turn restarts Node and pauses the broker, which no other journey may see.
ALONE = frozenset({"pins", "sandbox", "memory", "memory_session", "hub", "find_read"})
# R44SMALL-FU-4: MR-16's journey lowers the per-input cap for the services (a restart of Node, the worker and the query
# service), which no other journey may see.
ALONE_CELLS = frozenset({("attach", "job.opencode", "seeded")})
# A recovery journey kills the worker and the query service, which every other journey's run would see.
ALONE_PATHS = frozenset({"recovery"})

# --- the stack -------------------------------------------------------------------------------------------------------

sh: Any = None  # smoke_human, imported once the run dir is known (it reads E2E_STATE_DIR at import)
Stack: Any = None
StackError: type[Exception] = RuntimeError


def _import(run_dir: Path) -> None:
    global sh, Stack, StackError
    os.environ["E2E_STATE_DIR"] = str(run_dir.parent)
    sys.path.insert(0, str(HERE))
    import smoke_human

    smoke_human.RUN_DIR = run_dir
    sh, Stack, StackError = smoke_human, smoke_human.Stack, smoke_human.StackError


ENGINE_OF = {p: p.split(".", 1)[1] for p in JOB_PATHS}
# The engine an owner_pinned loop journey pins as the person's own choice: the release engine every lane can run.
PINNED_ENGINE = "opencode"
# The engines whose profile says a command's output may be lost (acp_agents.json outputMayBeLost, R32ENGFU-FU-1).
LOSES_OUTPUT = frozenset({"acp-qwen"})
SEEDED_CODERS = {"job.opencode": "agentOpencode", "job.codex": "agentCodex", "job.claude_code": "agentClaudeCode"}
ASK_TOOL, READ_TOOL = "create_ticket", "lookup_note"
NOTE = "Private note on {topic}: the finance review is due on Friday."
KB_PHRASE = "14 days ahead"
KB_QUERY = {"query": "travel policy"}


def kb_search(marker: str) -> str:
    """Knowledge reaches a model as a flat search, as the exploration helper (a deep sub-agent's), or as the graph
    search; the scripted model takes the first one offered (fake_llm _offered_alternative). The helper is offered the
    graph tools only and its requests never carry the turn's ``say`` (R31JOURNEY-FU-6): its goal names the graph search
    and carries the marker, so the page's text and the marker meet in the helper's own request (``/__control/reached``)."""
    query = json.dumps(KB_QUERY)
    helper = {"goal": f"Find the travel policy ({marker}).\ntool knowledgegraph__search {query} || "
                      f"search_internal_knowledge {query}\nsay found"}
    return ("tool " + " || ".join(f"{name} {json.dumps(args)}" for name, args in (
        ("search_internal_knowledge", KB_QUERY), ("internal_exploration_agent", helper),
        ("knowledgegraph__search", KB_QUERY))))
# Deep mode's intent step reads the request as one line (smoke_human LONG_ANSWER_LEAD): a leading sentence keeps the
# directives for the sub-agent.
DEEP_LEAD = "Please do the steps below."
HOLD_LOCK = threading.Lock()


@dataclass
class Turn:
    frames: list[Any]
    session: str
    run: str
    base: str
    agent: str | None
    child: str = ""
    extra: Json = field(default_factory=dict)

    @property
    def last(self) -> str:
        return self.frames[-1].type if self.frames else "none"

    def seen(self) -> str:
        """Everything the person's stream carried but the turn's own request: tool results, child steps, the answer."""
        return json.dumps([f.data for f in self.frames if f.type not in ("TOOL_CALL_ARGS", "TOOL_CALL_START")])

    def events(self, stack: Any) -> list[Json]:
        if "events" not in self.extra:
            self.extra["events"] = list(stack.journal(self.session)["events"]) if self.session else []
        return self.extra["events"]

    def spec(self, stack: Any, run: str | None = None) -> Json:
        run = run or self.run
        return next((((e.get("data") or {}).get("spec") or {}) for e in self.events(stack)
                     if e.get("type") == "run.spec_pinned" and e.get("runId") == run), {})


@dataclass(frozen=True)
class Ctx:
    stack: Any
    shape: Json
    path: str
    shape_id: str
    marker: str
    notes: Json = field(default_factory=dict)

    @property
    def api(self) -> Any:
        """Who drives the journey: the person whose account the shape is (the owner shapes are the admin's own; a saved
        agent's, seeded or service, is driven by the org's member, as most people who use one are members). Two
        people also halve Node's per-person admission rate the runner meets (ADMISSION_QUOTAS.admissionsPerUser)."""
        return PEOPLE.get("member") if not self.owner and self.path != "command" and PEOPLE.get("member") else self.stack.api

    @property
    def owner(self) -> bool:
        """The person's own account (their servers held whole), not a saved agent's grant (seeded, service)."""
        return self.shape_id in ("owner", "owner_pinned")

    @property
    def engine(self) -> str | None:
        return ENGINE_OF.get(self.path)

    @property
    def start_path(self) -> str:
        """The path a resume or recovery journey's run starts on."""
        if self.path.startswith("resume.") or self.path == "recovery":
            return "assistant.agent" if self.owner else "agent.quick"
        return self.path

    @property
    def loop(self) -> bool:
        return self.engine is None

    @property
    def agent(self) -> str | None:
        p = self.start_path
        if self.shape_id == "service":
            return SERVICE_AGENT["key"]
        if p in ("assistant.agent", "command", CHAT_MODE_PATH) or (self.engine and self.shape_id != "seeded") or (
                p == "surface.slack" and self.owner):
            return None
        if self.engine:
            return self.stack.seed[SEEDED_CODERS[p]]
        return self.stack.seed["agentLoop"]

    @property
    def mode(self) -> str:
        return "deep" if self.start_path in ("agent.deep", "loop.subagent") else "quick"

    @property
    def harness(self) -> str | None:
        return "legacy" if self.path == "classic" else None

    def call(self, tool: str, args: Json) -> list[str]:
        """A tool call as the path's model makes it: the loop finds an MCP tool first (D-LAZY-4), an engine names it."""
        line = f"tool {tool} {json.dumps(args)}"
        return [line] if self.engine or not tool.endswith((READ_TOOL, ASK_TOOL)) else [sh.discover(tool), line]

    @property
    def read_server(self) -> str:
        return self.shape["owner"]["firstUseServer"] if self.owner else sh.MCP_NAME

    def read_tool(self) -> str:
        """The read tool of the server the shape uses: the owner's second server by its full name, as both of the
        owner's servers offer it."""
        return READ_TOOL if self.read_server == sh.MCP_NAME else f"mcp_{self.read_server}_{READ_TOOL}"

    def ask_tool(self) -> str:
        """The write the org's tool rule asks for (seed_human: e2esaas create_ticket), by its full name where the
        shape holds two servers that both offer it."""
        return f"mcp_{sh.MCP_NAME}_{ASK_TOOL}" if self.owner else ASK_TOOL

    @property
    def skill(self) -> str:
        return self.shape["shapes"]["owner" if self.owner else "seeded"]["skill"]


def script(ctx: Ctx, lines: list[str], say: str) -> str:
    body = "\n".join([*lines, f"say {say}"])
    return f"{DEEP_LEAD}\n{body}" if ctx.mode == "deep" else body


def base_of(ctx: Ctx) -> str:
    return "/api/v1/conversations" if ctx.agent is None else f"/api/v1/agents/{ctx.agent}/conversations"


# Node admits so many runs a minute per person (429 admission_rate_user, "try again shortly"); four journeys at a time
# reach it, a person does not, so a refused start waits and tries again rather than failing a feature.
RATE_REFUSAL, RATE_WAIT_S, RATE_TRIES = "admission_rate", 10, 18


def paced(start: Callable[[], Any]) -> Any:
    for _ in range(RATE_TRIES - 1):
        try:
            out = start()
        except StackError as error:
            if RATE_REFUSAL not in str(error):
                raise
            time.sleep(RATE_WAIT_S)
            continue
        # A stream's refusal is its error; a plain request's (commands/run, a steered message) is its body.
        if getattr(out, "status", 0) == 429 and RATE_REFUSAL in str(getattr(out, "error", "")) + str(
                getattr(out, "body", "")):
            time.sleep(RATE_WAIT_S)
            continue
        return out
    return start()


# The scripted model's line that keeps a turn on the parent loop (fake_llm PARENT_LINE): with the person's own coding
# engine pinned, the default chat's own calls are what an owner_pinned loop journey proves (R33JGAPS-FU-4).
STAY_ON_PARENT = "stay-on-parent"


def body_of(ctx: Ctx, text: str, **extra: Any) -> Json:
    """A turn's request as the web app sends it on the journey's path."""
    if ctx.loop and ctx.shape_id == "owner_pinned":
        text = f"{STAY_ON_PARENT}\n{text}"
    body: Json = {"query": text, "chatMode": ctx.mode, "recordIds": [], "filters": {},
                  "modelKey": ctx.stack.seed["llmKey"], "modelName": "e2e-llm", **extra}
    if ctx.agent is None:
        body.update(chatMode=chat_mode(ctx), harness="durable")
    if ctx.harness:
        body["harness"] = ctx.harness
    return body


def chat_mode(ctx: Ctx) -> str:
    """The built-in assistant's mode: Agent mode, or on the chat-mode path the mode menu's search modes (the web
    probe asks the web one)."""
    if ctx.path != CHAT_MODE_PATH:
        return "agent"
    # The memory probe's second turn reads the web: the mode that offers web_search.
    return "web_search" if ctx.notes.get("turn") in ("web", "memory") else "internal_search"


def ids_of(frames: list[Any]) -> tuple[str, str]:
    session = run = ""
    for frame in frames:
        if frame.type == "CUSTOM" and frame.name == "conversation_created":
            value = frame.data.get("value") or {}
            session, run = session or value.get("conversationId", ""), run or value.get("runId", "")
        if frame.type == "RUN_STARTED" and not run and not frame.data.get("parentRunId"):
            run = frame.data.get("runId", "")
    return session, run


def card(frame: Any) -> bool:
    return frame.type == "CUSTOM" and frame.name == "approval_requested"


def stops(frame: Any) -> bool:
    """Where the reader stops: a card it answers, or a sign-in the run waits for, which no probe answers."""
    return card(frame) or (frame.type == "CUSTOM" and frame.name == "connect_requested")


# A card's answer: a decision, or the decision's whole body (a denial's reason, an approval for the conversation).
Decision = str | Json


def converse(ctx: Ctx, path: str, body: Json, decisions: list[Decision], *, session: str = "", run: str = "",
             api: Any = None, decided: Callable[[str, Json], bool] | None = None) -> Turn:
    """One request read to its end the way the web app reads it: each card answered with the next of ``decisions``
    (the last one repeats; none: the turn stops at its first card), the run's stream reattached after it. ``api``:
    the person who sends it (the journey's person when none)."""
    api = api or ctx.api
    stream = paced(lambda: api.stream("POST", path, body, stop=stops,
                                      headers={"x-request-id": f"journey-{secrets.token_hex(4)}"}))
    if stream.error and not stream.frames:
        raise StackError(f"stream {stream.status}: {stream.error}")
    found_session, found_run = ids_of(stream.frames)
    # Where the turn's run is while a later read of it blocks (a held model call): a probe may act on it meanwhile.
    ctx.notes["live"] = (session or found_session, found_run or run)
    return follow(ctx, session or found_session, found_run or run, list(stream.frames), decisions, api=api,
                  decided=decided)


def follow(ctx: Ctx, session: str, run: str, frames: list[Any], decisions: list[Decision], *, api: Any = None,
           decided: Callable[[str, Json], bool] | None = None) -> Turn:
    """The run's stream read on from ``frames`` (from its start when none) to its end, cards answered as decisions
    say; a card ``decided`` says is already answered is read past, not answered."""
    api = api or ctx.api
    base = base_of(ctx)
    if not frames and session and run:
        frames = list(api.stream("GET", f"{base}/{session}/runs/{run}/stream", stop=stops).frames)
    for answered in range(12):
        last = frames[-1] if frames else None
        if last is None or last.type in sh.TERMINAL or not run or not card(last) or not decisions:
            break
        value = last.data.get("value") or {}
        decision = decisions[min(answered, len(decisions) - 1)]
        answer = decision if isinstance(decision, dict) else {"decision": decision}
        if decided is None or not decided(session, value):
            api.ok("POST", f"{base}/{session}/runs/{value.get('childRunId') or run}/interactions/"
                           f"{value.get('interactionId')}/decision",
                   {**answer, "idempotencyKey": f"journey-{secrets.token_hex(6)}"})
        after = next((f.id for f in reversed(frames) if f.id), None)
        more = api.stream("GET", f"{base}/{session}/runs/{run}/stream",
                          headers={"Last-Event-ID": after} if after else None, stop=stops)
        frames.extend(more.frames)
    t = Turn(frames, session, run, base, ctx.agent)
    t.child = sh.handed_off(frames)
    return t


# --- paths that start a run elsewhere than the chat: a schedule, a signed webhook, PipesHub's own /mcp ---------------

SCHEDULES = "/api/v1/agent-harness/schedules"
SCHEDULED_FLAG = "ENABLE_SCHEDULED_RUNS"
# A schedule that fires at once: the person's Run now (the same firer as the cron's), or the cron's own next slot.
FAR_CRON = "0 9 1 1 *"


def next_slot() -> str:
    """A daily cron whose next slot is the minute after next (a schedule fires at most every five minutes)."""
    from datetime import datetime, timedelta

    slot = datetime.now(UTC) + timedelta(minutes=2)
    return f"{slot.minute} {slot.hour} * * *"


def start_scheduled(ctx: Ctx, text: str) -> tuple[str, str, str]:
    """A firing refused for the person's admission rate (``quota``, as a chat turn's 429) is tried again on a new
    schedule, as the runner paces a chat turn."""
    for _ in range(RATE_TRIES - 1):
        try:
            return fire_schedule(ctx, text)
        except StackError as error:
            if "refused: quota" not in str(error):
                raise
            time.sleep(RATE_WAIT_S)
    return fire_schedule(ctx, text)


def fire_schedule(ctx: Ctx, text: str) -> tuple[str, str, str]:
    """A schedule of the seeded agent whose input is the probe's script, fired (Run now, the cron's slot on the use
    journey, or a signed delivery); (conversation, run, how it fired)."""
    webhook = ctx.path == "scheduled.webhook"
    on_slot = not webhook and ctx.notes.get("turn") == "use"
    trigger: Json = {"kind": "webhook"} if webhook else {"kind": "cron", "cron": next_slot() if on_slot else FAR_CRON,
                                                         "timeZone": "UTC"}
    created = ctx.api.ok("POST", SCHEDULES, {
        "name": f"journey {ctx.path} {ctx.marker}", "agentKey": ctx.agent, "trigger": trigger,
        "input": {"query": text, "chatMode": ctx.mode, "modelKey": ctx.stack.seed["llmKey"], "modelName": "e2e-llm"}})
    schedule = str(created["schedule"]["id"])
    firings: list[Json] = []
    try:
        if webhook:
            from driver import webhook_headers

            event = {"event": "journey", "marker": ctx.marker}
            ctx.api.ok("POST", f"/api/v1/agent-harness/triggers/{schedule}/webhook", event,
                             headers=webhook_headers(created, event))
            how = "a signed webhook delivery"
        elif not on_slot:
            ctx.api.ok("POST", f"{SCHEDULES}/{schedule}/run", {},
                             headers={"Idempotency-Key": f"journey-{ctx.marker}"})
            how = "Run now"
        else:
            how = f"its cron slot {trigger['cron']}"

        def fired() -> bool:
            firings[:] = (ctx.api.ok("GET", f"{SCHEDULES}/{schedule}/firings") or {}).get("firings") or []
            refused = [f for f in firings if not f.get("runId") and f.get("settledAt")]
            if refused:
                raise StackError(f"the firing was {refused[0].get('status')}: {refused[0].get('reason')}")
            return any(f.get("runId") for f in firings)

        sh.wait_until(fired, timeout=200, what="the schedule to fire a run", every=2)
    finally:
        ctx.api.request("DELETE", f"{SCHEDULES}/{schedule}")
    firing = next(f for f in firings if f.get("runId"))
    return str(firing["conversationId"]), str(firing["runId"]), f"{how} ({firing.get('kind')})"


@contextmanager
def scheduled_runs(stack: Any) -> Iterator[None]:
    """The org's Labs flag for scheduled runs on for the run, put back after."""
    path = "/api/v1/configurationManager/platform/settings"

    def save(flags: Json, current: Json) -> None:
        body: Json = {"featureFlags": flags,
                      "fileUploadMaxSizeBytes": current.get("fileUploadMaxSizeBytes") or 30 * 1024 * 1024}
        if current.get("agentHarness") is not None:
            body["agentHarness"] = current["agentHarness"]
        stack.api.ok("POST", path, body)

    current = stack.api.ok("GET", path) or {}
    flags = dict(current.get("featureFlags") or {})
    if flags.get(SCHEDULED_FLAG) is not True:
        save({**flags, SCHEDULED_FLAG: True}, current)
    try:
        yield
    finally:
        if flags.get(SCHEDULED_FLAG) is not True:
            save({**((stack.api.ok("GET", path) or {}).get("featureFlags") or {}),
                  SCHEDULED_FLAG: flags.get(SCHEDULED_FLAG, False)}, stack.api.ok("GET", path) or {})


PATS: dict[str, dict[str, str]] = {}
PAT_LOCK = threading.Lock()
PEOPLE: dict[str, Any] = {}
PEOPLE_IDS: dict[str, str] = {}


def mcp_token(api: Any) -> str:
    """One personal access token per person for the run's /mcp journeys (revoked at the end, mcp_tokens)."""
    with PAT_LOCK:
        if api.email not in PATS:
            made = api.ok("POST", "/api/v1/personal-access-tokens", {"name": f"journeys-{secrets.token_hex(3)}",
                                                                     "expiryDays": 30})
            PATS[api.email] = {"id": str(made["token"]["id"]), "token": str(made["token"]["accessToken"]), "api": api}
        return PATS[api.email]["token"]


@contextmanager
def mcp_tokens(stack: Any) -> Iterator[None]:
    try:
        yield
    finally:
        for made in PATS.values():
            made["api"].request("DELETE", f"/api/v1/personal-access-tokens/{made['id']}")
        PATS.clear()


def mcp_call(api: Any, method: str, params: Json, timeout: float = 300) -> Json:
    """One JSON-RPC call to PipesHub's own /mcp as an external MCP client sends it (Streamable HTTP, stateless,
    a personal access token)."""
    import urllib.request

    body = json.dumps({"jsonrpc": "2.0", "id": secrets.token_hex(4), "method": method, "params": params}).encode()
    request = urllib.request.Request(f"{api.base}/mcp", data=body, method="POST", headers={
        "Authorization": f"Bearer {mcp_token(api)}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        raw = answer.read().decode()
    lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")] or [raw]
    return json.loads(lines[-1])


def mcp_result(answer: Json | None) -> Json:
    """What ``pipeshub_chat`` answered (its text content's JSON: conversationId, status, answer, …); {} for none."""
    content = (((answer or {}).get("result") or {}).get("content") or [{}])[0].get("text") or "{}"
    try:
        result = json.loads(content)
    except ValueError:
        return {"answer": content}
    return result if isinstance(result, dict) else {"answer": content}


def start_mcp(ctx: Ctx, text: str) -> tuple[str, str, str]:
    """The seeded agent asked through /mcp's pipeshub_chat; (conversation, run, what /mcp answered)."""
    mcp_call(ctx.api, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "journeys", "version": "1"}})
    def ask() -> Json:
        answered = mcp_call(ctx.api, "tools/call", {"name": "pipeshub_chat", "arguments": {
            "query": text, "agentId": ctx.agent, "modelKey": ctx.stack.seed["llmKey"], "modelName": "e2e-llm"}})
        # The person's admission rate reaches an MCP client as the tool's error text (the chat route's 429).
        if "Too many agent runs" in json.dumps(answered):
            raise StackError(f"{RATE_REFUSAL}: /mcp")
        return answered

    result = mcp_result(paced(ask))
    session = str(result.get("conversationId") or "")
    events = list(ctx.stack.journal(session)["events"]) if session else []
    run = next((str(e.get("runId")) for e in events if e.get("type") == "run.admitted"), "")
    return session, run, f"/mcp answered status {result.get('status')}: {str(result.get('answer'))[:80]!r}"


# --- R35-SLACK: the Slack bot, driven through the scripted Slack workspace (fake_saas/fake_slack.py) ----------------------

SLACK_BOTS = "/api/v1/configurationManager/slack-bot"
# The workspace's people: the admin (the owner shapes) and the org's member (a saved agent's journeys).
SLACK_PEOPLE: dict[str, str] = {}
# The journey's bot by shape: the default assistant's (no agent) for the owner shapes, the seeded agent's otherwise.
SLACK_BOT: dict[str, Json] = {}
# Slack sends a message as one line; the scripted model reads this as a line break (fake_llm SLACK_LINE_BREAK).
SLACK_LINE_BREAK = " ¶ "
# What makes a bot reply final: the answer (the turn's own words), the hand-off, which links the conversation, or a
# question the bot puts in the thread for the person to answer there.
HAND_OFF_LINK = "conversationId="
SLACK_QUESTION = "Reply in this thread with your answers"
# What the bot says when the person's admission rate refused the turn (the chat route's 429).
SLACK_RATE = "Too many agent runs were started for this user"
SLACK_REPLY_S, SLACK_ANSWER_S = 240, 120
SLACK_LINK_DECIDE = "/api/v1/configurationManager/slack-bot/identity-links/requests/decide"
SLACK_LINK_TOKEN = re.compile(r"/slack/link\?token=([A-Za-z0-9_-]{43})")
# R38-IDENT (ADR-SLACKID-01): people who must never act as anyone. Each carries the admin's own address in their
# Slack profile, as an attacker who controls a Slack profile would set it.
SLACK_SPOOFER, SLACK_OUTSIDER, SLACK_GUEST = "U0E2ESPOOF", "U0E2EOUTSIDER", "U0E2EGUEST"
SLACK_PARTNER_TEAM = "T0PARTNER"


def slack_private(stack: Any, user: str) -> list[str]:
    """What the bot told only this person (``chat.postEphemeral``), oldest first."""
    return [str(m.get("text") or "") for m in
            (stack.saas.ok("GET", f"/__control/slack/ephemeral?user={user}") or {}).get("messages") or []]


def slack_deliver_until_known(stack: Any, message: Json) -> Json:
    """A delivery the bot verified: a bot added a moment ago is known after its registry refresh (at most 30 s)."""
    sent: Json = {}

    def known() -> bool:
        sent.update(stack.saas.ok("POST", "/__control/slack/deliver", message) or {})
        return int(sent.get("status") or 0) != 401

    sh.wait_until(known, timeout=90, what="the bot to know its signing secret", every=5)
    return sent


def link_slack_person(stack: Any, api: Any, user: str, bot: Json) -> None:
    """The person links their Slack account once, as a person does: a direct message to the bot, the sign-in link it
    sends them privately, and the approval signed in to PipesHub. A person linked by an earlier run stays linked."""
    links = (stack.api.ok("GET", f"{SLACK_BOTS}/identity-links") or {}).get("links") or []
    if any(link.get("slackUserId") == user and link.get("active") for link in links):
        return
    before = len(slack_private(stack, user))
    slack_deliver_until_known(stack, {**bot, "user": user, "channel": "D0E2E", "text": "hi"})
    found: dict[str, str] = {}

    def prompted() -> bool:
        for text in slack_private(stack, user)[before:]:
            match = SLACK_LINK_TOKEN.search(text)
            if match:
                found["token"] = match.group(1)
                return True
        return False

    sh.wait_until(prompted, timeout=60, what=f"the bot's private sign-in link for {user}", every=2)
    answer = api.ok("POST", SLACK_LINK_DECIDE, {"token": found["token"], "action": "approve"}) or {}
    if answer.get("linked") is not True:
        raise StackError(f"linking {user} answered {answer}")


def slack_identity_holds(stack: Any, bot: Json) -> None:
    """SEC-C-03 on the lane: a Slack profile that carries the admin's address acts as no one; a person of another
    workspace (Slack Connect), a guest, and a home member in a shared channel are refused privately; none of them
    starts a conversation."""
    cases = [
        (SLACK_SPOOFER, {}, "link your Slack account"),
        (SLACK_OUTSIDER, {}, "only answer people in this Slack workspace"),
        (SLACK_GUEST, {}, "Guest accounts"),
        ("U0E2EADMIN", {"shared": True}, "channels shared with other organisations"),
    ]
    for user, extra, said in cases:
        before = len(slack_private(stack, user))
        sent = slack_deliver_until_known(stack, {**bot, "user": user, "channel": "C0E2E", "text": "summarise the board deck",
                                                 **extra})
        sh.wait_until(lambda u=user, b=before, w=said: any(w.lower() in t.lower() for t in slack_private(stack, u)[b:]),
                      timeout=60, what=f"the bot's private refusal to {user}", every=2)
        mapped = stack.mongo(f"const t = d.slack_conversations.findOne({{threadId: {json.dumps(sent['thread_ts'])}}});"
                             " print(t ? t.conversationId : '')").strip()
        if mapped:
            raise StackError(f"SEC-C-03: {user} started conversation {mapped}")
        print(f"  slack identity: {user}{' (shared channel)' if extra else ''} refused privately: {said!r}", flush=True)


# SEC-B-03: Slack control syntax in a model's answer, which Slack would turn into a mass mention or a disguised link.
SLACK_TOKENS = ("<!channel>", "<!here>", "<!subteam^S0E2E>")
SLACK_DISGUISED = "<https://attacker.example/login|https://pipeshub.example/login>"


def slack_answer_shown_as_text(stack: Any, bot: Json, user: str) -> None:
    """SEC-B-03 on the lane: a linked person's turn whose scripted answer carries Slack's control syntax and a link
    labelled as another host; the thread shows the syntax as text and no trusted-looking label."""
    marker = f"esc{secrets.token_hex(3)}"
    say = f"{marker} {' '.join(SLACK_TOKENS)} {SLACK_DISGUISED}"
    sent = slack_deliver_until_known(stack, {**bot, "user": user, "channel": "C0E2E",
                                             "text": f"{SLACK_LINE_BREAK}say {say}"})
    where = f"/__control/slack/thread?channel={sent['channel']}&ts={sent['thread_ts']}"

    def answer() -> str:
        return next((slack_words(m) for m in (stack.saas.ok("GET", where) or {}).get("messages") or []
                     if m.get("bot_id") and not m.get("deleted") and marker in slack_words(m)), "")

    sh.wait_until(lambda: bool(answer()), timeout=SLACK_REPLY_S, what="the answer with Slack syntax", every=2)
    live = [t for t in (*SLACK_TOKENS, "|https://pipeshub.example") if t in answer()]
    if live:
        raise StackError(f"SEC-B-03: the bot posted live Slack syntax {live}")
    print("  slack output: the answer's Slack syntax reached the thread as text", flush=True)


@contextmanager
def slack_workspace(stack: Any, journeys: list[Json]) -> Iterator[None]:
    """The default assistant's and the seeded agent's Slack bots configured as an admin configures them, and the
    people in the scripted workspace; the bots removed after."""
    if not any(j["path"] == "surface.slack" for j in journeys):
        yield
        return
    people = {"U0E2EADMIN": stack.api.email}
    apis = {"U0E2EADMIN": stack.api}
    if "member" in PEOPLE:
        people["U0E2EMEMBER"] = PEOPLE["member"].email
        apis["U0E2EMEMBER"] = PEOPLE["member"]
    workspace: dict[str, Json] = {u: {"email": e, "name": u[4:].title()} for u, e in people.items()}
    workspace[SLACK_SPOOFER] = {"email": stack.api.email, "name": "Spoof"}
    workspace[SLACK_OUTSIDER] = {"email": stack.api.email, "name": "Outsider", "team": SLACK_PARTNER_TEAM}
    workspace[SLACK_GUEST] = {"email": stack.api.email, "name": "Guest", "is_restricted": True}
    stack.saas.ok("PUT", "/__control/slack/users", workspace)
    SLACK_PEOPLE.update({e: u for u, e in people.items()})
    made: list[str] = []
    try:
        for shapes, agent in ((("owner", "owner_pinned"), ""), (("seeded",), stack.seed["agentLoop"])):
            bot = {"botToken": f"xoxb-e2e-{secrets.token_hex(4)}", "signingSecret": secrets.token_hex(16)}
            answer = stack.api.ok("POST", SLACK_BOTS, {"name": f"journeys {shapes[0]} {secrets.token_hex(2)}",
                                                       "agentId": agent, **bot})
            made.append(str(((answer or {}).get("config") or {}).get("id") or ""))
            for shape in shapes:
                SLACK_BOT[shape] = bot
        # ADR-SLACKID-01: a person acts as the PipesHub account they linked by signing in, never by profile email.
        default_bot = SLACK_BOT["owner"]
        slack_identity_holds(stack, default_bot)
        for user, api in apis.items():
            link_slack_person(stack, api, user, default_bot)
        slack_answer_shown_as_text(stack, default_bot, "U0E2EADMIN")
        yield
    finally:
        SLACK_BOT.clear()
        SLACK_PEOPLE.clear()
        for config in made:
            if config:
                stack.api.request("DELETE", f"{SLACK_BOTS}/{config}")


def slack_thread(ctx: Ctx) -> list[Json]:
    where = ctx.notes["slack"]
    return list((ctx.stack.saas.ok("GET", f"/__control/slack/thread?channel={where['channel']}&ts={where['thread']}")
                 or {}).get("messages") or [])


def slack_words(message: Json) -> str:
    """What a person reads of a bot message: its text, its blocks and a streamed answer's markdown."""
    return " ".join([str(message.get("text") or ""), json.dumps(message.get("blocks") or []),
                     str(message.get("markdown") or "")])


def slack_replies(ctx: Ctx) -> list[str]:
    return [slack_words(m) for m in slack_thread(ctx) if m.get("bot_id") and not m.get("deleted")]


def start_slack(ctx: Ctx, text: str) -> tuple[str, str, str]:
    """The journey's script sent to the shape's bot as an @mention in a channel, as a person in Slack sends it; read
    on until the bot's reply settles (the answer, or the hand-off to PipesHub); (conversation, run, what the thread
    holds then)."""
    say = ctx.notes.get("expect") or next((line[4:] for line in text.splitlines() if line.startswith("say ")), ctx.marker)
    ctx.notes["say"] = say
    session, run, sent = slack_start_thread(ctx, text)
    settled = True
    try:
        sh.wait_until(lambda: any(say in r or HAND_OFF_LINK in r or SLACK_QUESTION in r for r in slack_replies(ctx)),
                      timeout=SLACK_REPLY_S,
                      what="the bot's reply", every=2)
    except StackError:
        settled = False
    replies = slack_replies(ctx)
    ctx.notes["slackHandOff"] = any(HAND_OFF_LINK in r for r in replies)
    how = (f"an @mention in Slack (thread {sent['thread_ts']}); the bot's reply "
           + ("settled" if settled else f"did not settle within {SLACK_REPLY_S} s")
           + f": {[r[:160] for r in replies][-2:]}")
    return session, run, how


def slack_start_thread(ctx: Ctx, text: str) -> tuple[str, str, Json]:
    """The script sent to the shape's bot as an @mention in a channel, read until the bot made the thread a
    conversation; (conversation, its first run, the delivery)."""
    bot = SLACK_BOT[ctx.shape_id]
    # The script starts on a line of its own: the message opens with the bot's mention.
    message = {**bot, "user": SLACK_PEOPLE[ctx.api.email], "channel": "C0E2E",
               "text": ("\n" + text).replace("\n", SLACK_LINE_BREAK).strip()}

    def deliver() -> Json:
        sent = ctx.stack.saas.ok("POST", "/__control/slack/deliver", message)
        # The bot refreshes its registry for an unknown signature at most every 30 s: a new bot may wait that long.
        if int(sent.get("status") or 0) == 401:
            raise StackError(f"{RATE_REFUSAL}: the bot does not know its signing secret yet")
        return sent

    found: dict[str, str] = {}

    def started_thread() -> Json:
        """A delivery the bot turned into a conversation; a person's admission rate (the bot says so in the thread)
        is waited out and the message sent again, as a person in Slack would."""
        sent = deliver()
        ctx.notes["slack"] = {"channel": sent["channel"], "thread": sent["thread_ts"]}

        def mapped() -> bool:
            out = ctx.stack.mongo(f"const t = d.slack_conversations.findOne({{threadId: {json.dumps(sent['thread_ts'])}}});"
                                  " print(t ? t.conversationId : '')").strip()
            found["session"] = out
            if not out and any(SLACK_RATE in r for r in slack_replies(ctx)):
                raise StackError(f"{RATE_REFUSAL}: {SLACK_RATE}")
            return bool(out)

        sh.wait_until(mapped, timeout=120, what="the bot's conversation for the thread", every=2)
        return sent

    sent = paced(started_thread)
    session = found["session"]
    run = ""

    def admitted() -> bool:
        nonlocal run
        events = list(ctx.stack.journal(session)["events"])
        run = next((str(e.get("runId")) for e in events if e.get("type") == "run.admitted"), "")
        return bool(run)

    with suppress(StackError):
        sh.wait_until(admitted, timeout=30, what="the thread's run to be admitted", every=1)
    return session, run, sent


def slack_reply(ctx: Ctx, text: str) -> Json:
    """The person's reply in the journey's thread; one the bot answers with the person's admission rate is sent
    again once the rate allows, as a person would."""
    where = ctx.notes["slack"]
    for _ in range(RATE_TRIES):
        before = len(slack_replies(ctx))
        sent = ctx.stack.saas.ok("POST", "/__control/slack/deliver", {
            **SLACK_BOT[ctx.shape_id], "user": SLACK_PEOPLE[ctx.api.email], "channel": where["channel"],
            "thread_ts": where["thread"], "text": text})
        try:
            sh.wait_until(lambda: len(slack_replies(ctx)) > before, timeout=60, what="the bot's answer", every=2)
        except StackError:
            return sent
        if not any(SLACK_RATE in r for r in slack_replies(ctx)[before:]):
            return sent
        time.sleep(RATE_WAIT_S)
    return sent


def slack_answered(ctx: Ctx) -> tuple[bool, str]:
    """Whether the thread ends with the turn's answer: the bot's own when its stream saw the end, else the one posted
    there once the run went on after the person decided in PipesHub."""
    say = ctx.notes.get("say") or ctx.marker
    try:
        sh.wait_until(lambda: any(say in r for r in slack_replies(ctx)), timeout=SLACK_ANSWER_S,
                      what="the answer in the Slack thread", every=2)
        ok = True
    except StackError:
        ok = False
    replies = slack_replies(ctx)
    last = replies[-1][:160] if replies else "none"
    return ok, (f"Slack thread: answer {say!r} posted {ok}, handed off to PipesHub {ctx.notes.get('slackHandOff')}, "
                f"{len(replies)} bot message(s), last {last!r}")


STARTED_ELSEWHERE = {"scheduled.cron": start_scheduled, "scheduled.webhook": start_scheduled, "surface.mcp": start_mcp,
                     "surface.slack": start_slack}


# A resume journey's run first stops for the person (RESUME_FIRST), then goes on with the probe's own script: after an
# approved card the script is the same turn's; after a question it is the person's answer, which the run reads as the
# turn's next message (the scripted model takes directives from the current turn's user messages).
QUESTION = {"questions": [{"question": "Which colour should the chart use?",
                           "options": [{"label": "Teal"}, {"label": "Amber"}], "multiSelect": False}]}


def run_turn(ctx: Ctx, text: str, *, decide: Decision | None = None, pause: bool = True,
             decided: Callable[[str, Json], bool] | None = None) -> Turn:
    """The probe's turn on the journey's path. On a resume path (``pause``) the run first stops for the person and
    resumes; ``ctx.notes["resumed"]`` says whether it did, and ``t.extra["pauseCards"]`` how many of the turn's cards
    were the pause's own."""
    decisions: list[Decision] = [decide] if decide else []
    if ctx.path in STARTED_ELSEWHERE:
        session, run, how = STARTED_ELSEWHERE[ctx.path](ctx, text)
        t = follow(ctx, session, run, [], decisions)
        t.extra["started"] = how
        ctx.notes["started"] = f"{how}, run {run or 'none'}"
        if ctx.path == "surface.slack":
            # The person in Slack reads the answer in the thread, whether the run asked a card on the way or not.
            ctx.notes["slackAnswer"] = slack_answered(ctx)
        return t
    if ctx.path == "recovery":
        return recovered_turn(ctx, text, decisions)
    if pause and ctx.path == "resume.approval":
        first = ctx.call(ctx.ask_tool(), {"title": f"resume {ctx.marker}", "body": "journey"})
        t = converse(ctx, f"{base_of(ctx)}/stream", body_of(ctx, "\n".join([*first, text])),
                     ["approved", *(decisions or ["approved"])])
        ctx.notes["resumed"] = bool(cards_of(t)) and effects_of(ctx, {"title": f"resume {ctx.marker}",
                                                                       "body": "journey"}) == 1
        t.extra["pauseCards"] = 1
        return t
    if pause and ctx.path == "resume.question":
        ask = converse(ctx, f"{base_of(ctx)}/stream",
                       body_of(ctx, script(ctx, [f"tool ask_user_question {json.dumps(QUESTION)}"], f"q-{ctx.marker}")),
                       [])
        questioned = any(f.type == "CUSTOM" and "question" in str(f.name) for f in ask.frames)
        if not ask.session or not ask.run:
            ctx.notes["resumed"] = False
            return ask
        t = converse(ctx, f"{ask.base}/{ask.session}/messages/stream",
                     body_of(ctx, f"Teal\n{text}", answers=ask.run), decisions, session=ask.session, run=ask.run)
        t.frames = [*ask.frames, *t.frames]
        t.child = sh.handed_off(t.frames)
        ctx.notes["resumed"] = questioned and t.last in sh.TERMINAL
        return t
    return converse(ctx, f"{base_of(ctx)}/stream", body_of(ctx, text), decisions, decided=decided)


def started(frames_seen: list[Any]) -> Callable[[Any], bool]:
    def stop(frame: Any) -> bool:
        frames_seen.append(frame)
        return frame.type == "RUN_STARTED"
    return stop


def start_held(ctx: Ctx, say: str, text: str | None = None) -> Turn:
    """A turn whose model call holds (``hold``), read until its run started; the caller releases the hold."""
    body = {"query": text or script(ctx, ["hold"], say), "chatMode": ctx.mode, "recordIds": [], "filters": {},
            "modelKey": ctx.stack.seed["llmKey"], "modelName": "e2e-llm"}
    if ctx.agent is None:
        body.update(chatMode=chat_mode(ctx), harness="durable")
    if ctx.harness:
        body["harness"] = ctx.harness
    # A hold is keyed by its place in a conversation and spent once (fake_llm control_reset): a fresh brain first.
    ctx.stack.model.request("POST", "/__control/reset", {})
    seen: list[Any] = []
    def start() -> Any:
        seen.clear()
        return ctx.api.stream("POST", f"{base_of(ctx)}/stream", body, stop=started(seen),
                                    headers={"x-request-id": f"journey-{secrets.token_hex(4)}"})

    stream = paced(start)
    session = run = ""
    for frame in stream.frames:
        if frame.type == "CUSTOM" and frame.name == "conversation_created":
            value = frame.data.get("value") or {}
            session, run = session or value.get("conversationId", ""), run or value.get("runId", "")
        if frame.type == "RUN_STARTED" and not run:
            run = frame.data.get("runId", "")
    return Turn(list(stream.frames), session, run, base_of(ctx), ctx.agent)


def holding(ctx: Ctx, timeout: float = 120) -> bool:
    try:
        sh.wait_until(lambda: (ctx.stack.model.ok("GET", "/__control/holding") or {}).get("holding") is True,
                      timeout=timeout, what="the scripted model to hold", every=1)
        return True
    except StackError:
        return False


def release(ctx: Ctx) -> None:
    ctx.stack.model.request("POST", "/__control/release", {})


def release_held(ctx: Ctx) -> bool:
    """Lets the scripted model's held call go only when one is held (R50JFIX-8): a release with nothing held must not
    reach the model, whose next hold it once let through. Whether one was."""
    if (ctx.stack.model.ok("GET", "/__control/holding") or {}).get("holding") is not True:
        return False
    release(ctx)
    return True


TERMINAL_EVENTS = {"run.completed": "completed", "run.failed": "failed", "run.cancelled": "cancelled",
                   "run.quarantined": "quarantined"}


def run_status(ctx: Ctx, session: str, run: str) -> str:
    """The run's ending as its journal says it ("running" until a terminal event; "erased" once a delete erased it,
    which ends the run first, ADR-RUN-05)."""
    try:
        events = list(ctx.stack.journal(session)["events"]) if session else []
    except StackError as error:
        if "deleted and its data erased" in str(error):
            return "erased"
        raise
    return next((TERMINAL_EVENTS[e["type"]] for e in events if e.get("runId") == run and e.get("type") in TERMINAL_EVENTS),
                "running")


def ended(ctx: Ctx, session: str, run: str, timeout: float = 60) -> str:
    """The run's ending once its journal has one (or "running" after ``timeout``)."""
    status = "running"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = run_status(ctx, session, run)
        if status != "running":
            return status
        time.sleep(2)
    return status


# --- probes: each turn and what it proves ----------------------------------------------------------------------------

Line = tuple[str, bool, str]  # feature, ok, detail


def use_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    topic = f"journey-{ctx.marker}"
    lines: list[str] = []
    if "mcp.named" in features or "mcp.first_use" in features:
        lines += ctx.call(ctx.read_tool(), {"topic": topic})
    if "knowledge" in features:
        lines.append(kb_search(f"done-{ctx.marker}"))
        watch_reached(ctx, f"done-{ctx.marker}")
    if "skills.load" in features:
        lines.append(f"tool load_skill {json.dumps({'name': ctx.skill})}")
    if ctx.engine and ("skills.preload" in features or "skills.load" in features):
        # The engine loads the skill its own way: its skill tool, or (Codex) its shell on the SKILL.md it listed.
        lines.append(f"skill {ctx.skill}")
    artifact = f"journey-{ctx.marker}.txt"
    content = f"made-{secrets.token_hex(4)}"
    missing = f"missing-{ctx.marker}.txt"
    if "artifacts" in features and not ctx.engine:
        lines.append(save_line(artifact, content))
    elif "artifacts" in features:
        lines += [f"write {artifact} {content}", f"shell cat {artifact}"]
        if ctx.engine:
            # A command that fails reads failed with its code, not exit 0 (R31JFIX-FU-1: dsh said it only in its text).
            lines.append(f"shell cat {missing}")
    say = f"done-{ctx.marker}"
    said = say
    linked = f"fib{ctx.marker}.py"
    if "artifacts" in features and ctx.engine:
        # MR-18 (R36LIVE-FU-3): the job's answer links its file by its sandbox path, as Codex did in a manual test.
        lines.append(f"write out/{linked} print('fib')")
        said = f"{say} [Download {linked}](sandbox:/mnt/data/out/{linked})"
    t = run_turn(ctx, script(ctx, lines, said))
    finished = t.last == "RUN_FINISHED" and say in sh.answer(t.frames)
    head = f"conversation {t.session} run {t.run} ended {t.last}" + (f", child {t.child}" if t.child else "")
    out: list[Line] = []
    child_ok, child_detail = child_check(ctx, t)
    seen = t.seen()
    for f in features:
        if f in ("mcp.named", "mcp.first_use"):
            result = NOTE.format(topic=topic) in seen
            ok, detail = finished and child_ok and result, f"{head}; {child_detail}; {ctx.read_tool()} result seen: {result}"
            if f == "mcp.first_use":
                whole, held = held_whole(ctx, t)
                ok, detail = ok and whole, f"{detail}; parent holds whole {held}"
                if ctx.engine:
                    env = sh.tool_environment(t.child) if t.child else ""
                    servers = env.split(" mcpServers=", 1)[1].split()[0] if " mcpServers=" in env else "?"
                    want = str(len(ctx.shape["owner"]["servers"]))
                    ok, detail = ok and servers == want, f"{detail}; child mcpServers={servers} (want {want})"
            out.append((f, ok, detail))
        elif f == "knowledge":
            found, where = knowledge_found(seen, reached_phrases(ctx, say))
            out.append((f, finished and child_ok and found,
                        f"{head}; {child_detail}; the page's text reached the model: {found} ({where})"))
        elif f == "skills.load" and ctx.engine:
            # On a job the skill is loaded on demand by the engine's own skill tool (or Codex's shell on its SKILL.md):
            # the job's step that read it is what the person sees.
            read = skill_read(t, ctx)
            detail = f"{head}; {child_detail}; a job step loaded {ctx.skill} on demand: {read}"
            out.append((f, finished and child_ok and read, detail))
        elif f == "skills.load":
            loaded = [x for x in t.frames if x.type == "TOOL_CALL_RESULT" and ctx.skill in json.dumps(x.data)
                      and x.data.get("status") != "failed"]
            out.append((f, finished and bool(loaded), f"{head}; load_skill {ctx.skill} answered: {bool(loaded)}"))
        elif f == "skills.preload":
            pinned = [s.get("name") for s in ((t.spec(ctx.stack, t.child or t.run).get("extensions") or {})
                                              .get("x.pipeshub") or {}).get("skills") or []]
            ok = finished and ctx.skill in pinned
            detail = f"{head}; pinned skills {pinned}"
            if ctx.engine:
                # Mounted in the job's sandbox (its assembly counts the skills), and what the person sees: a step of
                # the job that read the skill ("Read the PDF skill"), R31GVENG-FU-2.
                assembly = next((x for x in sh.worker_lines(t.child) if "engine.assembly" in x), "") if t.child else ""
                mounted = assembly.split(" skills=", 1)[1].split()[0] if " skills=" in assembly else "?"
                read = skill_read(t, ctx)
                steps = sh.child_steps(t.frames, t.child) if t.child else []
                kinds = sorted({f"{v.get('kind')}:{str(v.get('text') or '')[:40]}" for v in steps})
                ok = ok and read and mounted not in ("0", "?")
                detail = f"{detail}; mounted skills={mounted}; a job step read the skill: {read} (steps {kinds})"
            out.append((f, ok, detail))
        elif f == "artifacts" and not ctx.engine:
            listed = artifact_listed(ctx, t.session, artifact, content)
            out.append((f, finished and child_ok and listed,
                        f"{head}; {child_detail}; the saved file {artifact} is listed in the conversation's artifacts "
                        f"with its content's size: {listed}"))
        elif f == "artifacts":
            steps = sh.child_steps(t.frames, t.child) if t.child else []
            changed = any(v.get("kind") in ("file", "diff") and str(v.get("text") or "").endswith(artifact) for v in steps)
            output = content in "".join(str(v.get("text") or "") for v in steps if v.get("kind") == "output")
            lost = not output and lost_output(ctx, t, f"cat {artifact}")
            kinds = sorted({str(v.get("kind")) for v in steps})
            outputs = [str(v.get("text"))[:60] for v in steps if v.get("kind") == "output"]
            failed = [(v.get("data") or {}) for v in steps if v.get("kind") == "command_done"
                      and (v.get("data") or {}).get("status") == "failed"]
            exits = [d.get("exitCode") for d in failed]
            failed_ok = any(isinstance(code, int) and code != 0 for code in exits) and not any(
                "exit code:" in str(v.get("text") or "") for v in steps if v.get("kind") == "output")
            # R32ENGFU-FU-1: only an engine known to lose output marks its commands, so an empty one never reads
            # "No output." there; every other engine's commands are what they printed.
            marked = {bool((v.get("data") or {}).get("outputMayBeLost")) for v in steps if v.get("kind") == "command"}
            marks_ok = marked == {ctx.engine in LOSES_OUTPUT}
            detail = (f"{head}; {child_detail}; file change shown {changed}, its content read back "
                      f"{OUTPUT_LOST if lost else output}, "
                      f"the failed cat read failed {failed_ok} (exit codes {exits}), commands marked output-may-be-lost "
                      f"{sorted(marked)} (step kinds {kinds}, outputs {outputs})")
            link_ok, link_detail = sandbox_link_verdict(t.events(ctx.stack), linked, t.frames, t.run)
            detail += f"; {link_detail}"
            out.append((f, finished and child_ok and changed and (output or lost) and failed_ok and marks_ok and link_ok,
                        detail))
    return out


def link_texts(events: list[Json], name: str, frames: list[Any] = (), root: str = "") -> list[tuple[str, str]]:
    """What the person reads that names ``name``, each as (where, text): the text streamed to them (each message's
    projected ``TEXT_MESSAGE_CONTENT``), a job's own messages, its presented result and the finalized answer. The root
    loop's journaled ``message.completed`` is its model's raw transcript, which Node's projector shows through its text
    filter (R50JFIX-2), so the root is read as streamed."""
    import base64

    texts: list[tuple[str, str]] = []
    streamed: dict[str, str] = {}
    for f in frames:
        if f.type == "TEXT_MESSAGE_CONTENT":
            key = str(f.data.get("messageId") or "")
            streamed[key] = streamed.get(key, "") + str(f.data.get("delta") or "")
    texts.extend(("streamed", t) for t in streamed.values())
    for e in events:
        d = e.get("data") or {}
        if e.get("type") == "message.completed" and d.get("role") == "assistant" and (not root or e.get("runId") != root):
            ref = str((d.get("textRef") or {}).get("ref") or "")
            if ref.startswith("inline:"):
                texts.append(("message", base64.b64decode(ref[len("inline:"):]).decode(errors="replace")))
        elif e.get("type") == "x.pipeshub.tool.result_presented":
            texts.append(("tool_result", str(d.get("content"))))
        elif e.get("type") == "x.pipeshub.answer.finalized":
            texts.append(("finalized", str(d.get("answer") or "")))
    return [(k, t) for k, t in texts if name in t]


def sandbox_link_verdict(events: list[Json], name: str, frames: list[Any] = (), root: str = "") -> tuple[bool, str]:
    """MR-18: a ``sandbox:`` link to a file the job saved reaches the person as the saved artifact's link (``/record/``),
    never as a link the chat cannot open (a link to a file nobody saved is dropped instead: unit-held only)."""
    mine = link_texts(events, name, frames, root)
    raw = [k for k, t in mine if "sandbox:" in t]
    opened = [k for k, t in mine if "/record/" in t]
    ok = bool(mine) and not raw and bool(opened)
    return ok, (f"the sandbox link to {name}: named in {[k for k, _ in mine]}, still sandbox: in {raw or 'none'} "
                f"(want none), opens the saved file in {opened or 'none'}")


def save_line(name: str, content: str) -> str:
    """A loop run saves a file the person can open: save_artifact, or where it is not offered (a deep sub-agent's tools
    are its domain helpers) the coding helper's run_code writing it to $OUTPUT_DIR, which is captured the same way."""
    code = {"language": "python", "code": f"import os\nopen(os.path.join(os.environ['OUTPUT_DIR'], {name!r}), 'w')"
                                          f".write({content!r})"}
    helper = {"goal": f"Write the file.\ntool run_code {json.dumps(code)}\nsay wrote"}
    return (f"tool save_artifact {json.dumps({'name': name, 'content': content})} || run_code {json.dumps(code)} || "
            f"coding_agent {json.dumps(helper)}")


def artifact_listed(ctx: Ctx, session: str, name: str, content: str | None) -> bool:
    """The conversation's artifacts, as the person's Files list reads them, hold ``name`` with the content's size
    (``None``: any size)."""
    if not session:
        return False
    found: list[Json] = []

    def listed() -> bool:
        items = (ctx.api.ok("GET", f"/api/v1/artifacts?conversationId={session}") or {}).get("items") or []
        found[:] = [i for i in items if i.get("name") == name]
        return bool(found)

    try:
        sh.wait_until(listed, timeout=30, what="the artifact to be listed", every=2)
    except StackError:
        return False
    return content is None or any(int(i.get("sizeInBytes") or -1) == len(content.encode()) for i in found)


# R34FIX-FU-3: the run's record of a command whose engine reported no output at all (Qwen Code's exit-before-data race,
# R31JFIX-FU-2, about one shell command in 20 on runsc). Words the probes put in a line so a run's count is greppable.
OUTPUT_LOST = "output lost upstream, the step says so"


def lost_output(ctx: Ctx, t: Turn, command: str) -> bool:
    """Whether the job ran ``command`` to exit 0 and its engine, one known to lose output (``LOSES_OUTPUT``), reported
    none on either stream, with the step marked so the person reads "No output was captured." rather than an empty
    result: the honest wording, the only way an unread output may pass. Any output, or no mark, is not this."""
    if ctx.engine not in LOSES_OUTPUT:
        return False
    for event in t.events(ctx.stack):
        data = event.get("data") or {}
        if event.get("type") != "x.pipeshub.command.finished" or data.get("command") != command:
            continue
        if (data.get("status") == "completed" and data.get("exitCode") == 0 and data.get("outputMayBeLost") is True
                and not (data.get("stdout") or {}).get("bytes") and not (data.get("stderr") or {}).get("bytes")):
            return True
    return False


def watch_reached(ctx: Ctx, marker: str) -> None:
    """Asks the scripted model to note when the seeded page's text reaches it in a request of this turn."""
    ctx.stack.model.request("POST", "/__control/reached", {"marker": marker, "phrase": KB_PHRASE})


def reached_phrases(ctx: Ctx, marker: str) -> list[str] | None:
    """What the scripted model saw reach it for this turn; ``None``: a stack whose model keeps no such record."""
    answered = ctx.stack.model.request("GET", f"/__control/reached?marker={marker}")
    body = answered.body if answered.status == 200 else None
    return list(body.get("reached") or []) if isinstance(body, dict) and "reached" in body else None


def knowledge_found(seen: str, reached: list[str] | None) -> tuple[bool, str]:
    """Whether the search gave the model the seeded page's text (R31CAPFIX-FU-1). Read where it is whole: the model's
    own request. The person's stream carries a tool result's first 2000 characters only (TOOL_CALL_RESULT), and the
    records are listed by record id, not relevance (``relevance_ordered_context`` is off by default), so the page is
    past the cut whenever its random id sorts late: the stream alone was red on some fresh stacks, green on others."""
    if reached is not None and KB_PHRASE in reached:
        return True, "in the model's request"
    if KB_PHRASE in seen:
        return True, "in the stream"
    return False, "not in the model's request" if reached is not None else "the model keeps no record; not in the stream"


def skill_read(t: Turn, ctx: Ctx) -> bool:
    steps = sh.child_steps(t.frames, t.child) if t.child else []
    return any(ctx.skill in ((v.get("data") or {}).get("skills") or []) for v in steps)


def cards_of(t: Turn) -> list[Json]:
    """The turn's approval cards but a resume path's own pause."""
    found = [f.data.get("value") or {} for f in t.frames if card(f)]
    return found[int(t.extra.get("pauseCards", 0)):]


def child_check(ctx: Ctx, t: Turn) -> tuple[bool, str]:
    """A job ran on the path's engine; a sub-agent path ran a child; another path ran no child."""
    if ctx.engine:
        engine = sh.child_engine(t.child) if t.child else ""
        return bool(t.child) and engine == ctx.engine, f"job engine {engine or 'none'} (want {ctx.engine})"
    if ctx.path == "loop.subagent":
        return bool(t.child), f"sub-agent {t.child or 'none'}"
    return True, "no child expected"


def held_whole(ctx: Ctx, t: Turn) -> tuple[bool, list[str]]:
    """Whether the parent pinned each of the owner's servers whole (``tools: at_first_use``)."""
    mine = ctx.stack.api.ok("GET", "/api/v1/mcp-servers/my-mcp-servers") or {}
    names = {str(i.get("_id") or i.get("instanceId")): i.get("name") for i in mine.get("instances") or []}
    pins = ((t.spec(ctx.stack).get("extensions") or {}).get("x.pipeshub") or {}).get("mcp") or []
    held = sorted(str(names.get(p.get("instanceId"), p.get("instanceId"))) for p in pins if p.get("tools") == "at_first_use")
    return set(ctx.shape["owner"]["servers"]) <= set(held), held


def effects_of(ctx: Ctx, args: Json) -> int:
    """The scripted SaaS's writes of exactly these arguments (it keeps their digest, never the values)."""
    digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]
    return sum(1 for e in sh.saas(ctx.stack, "effects") if e.get("argsDigest") == digest)


def card_turn(ctx: Ctx, features: list[str], decision: str) -> list[Line]:
    title = f"journey {decision} {ctx.marker}"
    say = f"after-{ctx.marker}"
    args = {"title": title, "body": "journey"}
    t = run_turn(ctx, script(ctx, ctx.call(ctx.ask_tool(), args), say), decide=decision)
    cards = cards_of(t)
    effects = effects_of(ctx, args)
    status = ended(ctx, t.session, t.run) if not ctx.harness else ("ended" if t.last in sh.TERMINAL else "running")
    head = f"conversation {t.session} run {t.run} ended {t.last} ({status}), cards {len(cards)}, effects {effects}"
    child_ok, child_detail = child_check(ctx, t)
    rule = rule_of(cards[0]) if cards else ""
    out: list[Line] = []
    for f in features:
        if f == "approval.ask":
            out.append((f, bool(cards) and child_ok, f"{head}; {child_detail}"))
        elif f == "approval.block":
            ok = effects == 0 and status in ("completed", "failed", "cancelled", "ended") and (bool(cards) or ctx.path == "classic")
            out.append((f, ok, f"{head}; nothing sent: {effects == 0}"))
        elif f == "approval.org_rule":
            ok = (bool(cards) and "org" in rule) or (ctx.path == "classic" and effects == 0)
            out.append((f, ok, f"{head}; the card's rule {rule or 'none'}"))
        elif f == "approval.allow":
            ok = bool(cards) and effects == 1 and t.last == "RUN_FINISHED" and say in sh.answer(t.frames) and child_ok
            detail = f"{head}; {child_detail}; written once {effects == 1}, answered after it {say in sh.answer(t.frames)}"
            out.append((f, ok, detail))
    return out


def rule_of(card: Json) -> str:
    """The layer and rule the card names, as the person is told who requires the approval."""
    reason = card.get("reason") or {}
    parts = [str(reason.get(k)) for k in ("layer", "setBy", "source", "rule", "code") if reason.get(k)]
    return "/".join(parts) or json.dumps(reason)[:120]


def slack_question_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """The run asks the person back in a Slack thread: the bot puts the question in the thread, the person answers
    there, and the thread gets the answer that goes on from it."""
    say = f"answered-{ctx.marker}"
    session, run, how = start_slack(ctx, script(ctx, [f"tool ask_user_question {json.dumps(QUESTION)}"], f"q-{ctx.marker}"))
    asked = any(QUESTION["questions"][0]["question"] in r for r in slack_replies(ctx))
    sent = slack_reply(ctx, f"Teal{SLACK_LINE_BREAK}say {say}")
    ctx.notes["say"] = say
    answered, said = slack_answered(ctx)
    ok = asked and answered and sent.get("status") == 200
    detail = (f"{how}; conversation {session} run {run}; the question was put in the thread {asked}; the person "
              f"answered there and the thread got the answer {answered}; {said}")
    return [(f, ok, detail) for f in features]


def question_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    if ctx.path == "surface.slack":
        return slack_question_turn(ctx, features)
    say = f"answered-{ctx.marker}"
    # On resume.question the question is the probe itself, not a pause before it.
    # A job asks through its gateway's ask_user, which its parent puts to the person as its own question.
    asking = (f"tool ask_user {json.dumps({'question': QUESTION['questions'][0]['question'], 'options': ['Teal', 'Amber']})}"
              if ctx.engine else f"tool ask_user_question {json.dumps(QUESTION)}")
    t = run_turn(ctx, script(ctx, [asking], say), pause=ctx.path != "resume.question")
    asked_q = any(f.type == "CUSTOM" and "question" in str(f.name) for f in t.frames)
    answered = converse(ctx, f"{t.base}/{t.session}/messages/stream", body_of(ctx, f"Teal\nsay {say}", answers=t.run),
                        [], session=t.session, run=t.run) if t.session else None
    frames = answered.frames if answered else []
    said = say in sh.answer(frames)
    end = frames[-1].type if frames else "none"
    detail = (f"conversation {t.session} run {t.run} ended {t.last}, question asked {asked_q}; answer stream "
              f"ended {end}, went on to its answer {said}")
    return [(f, asked_q and said and end == "RUN_FINISHED", detail) for f in features]


def saved_answers(ctx: Ctx, path: str) -> str:
    """The conversation's saved messages but the person's own, once it settles, read as its person."""
    def body() -> Json:
        return ((ctx.api.ok("GET", path) or {}).get("conversation") or {})

    try:
        sh.wait_until(lambda: body().get("status") not in (None, "Inprogress", "In Progress", "inprogress"),
                      timeout=60, what="the conversation to settle", every=2)
    except StackError:
        pass
    return json.dumps([m for m in body().get("messages") or [] if m.get("messageType") != "user_query"])


def resumed_cancel_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R31JOURNEY-FU-6: the run stops for the person (a card, a question), goes on, and is cancelled while it waits on
    the model: it ends cancelled, and the write the person allowed before stays done once."""
    say = f"never-{ctx.marker}"
    box: dict[str, Any] = {}
    with HOLD_LOCK:
        ctx.stack.model.request("POST", "/__control/reset", {})
        worker = threading.Thread(target=lambda: box.update(turn=run_turn(ctx, script(ctx, ["hold"], say))), daemon=True)
        worker.start()
        answer = None
        try:
            held = holding(ctx, timeout=240)
            session, run = live_run(ctx)
            if held and session and run:
                answer = ctx.api.request("POST", f"{base_of(ctx)}/{session}/runs/{run}/cancel",
                                         {"idempotencyKey": f"journey-{secrets.token_hex(6)}"})
        finally:
            release(ctx)
        worker.join(timeout=240)
    session, run = live_run(ctx)
    status = ended(ctx, session, run) if run else "none"
    ok = held and answer is not None and answer.status < 300 and status == "cancelled"
    detail = (f"conversation {session} run {run or 'none'} held after resuming {held}, cancel "
              f"{answer.status if answer else 'not sent'}, run {status}")
    return [(f, ok, detail) for f in features]


def live_run(ctx: Ctx) -> tuple[str, str]:
    """The conversation the turn is in and its newest run: answering a question admits the run that goes on."""
    session = (ctx.notes.get("live") or ("", ""))[0]
    if not session:
        return "", ""
    events = list(ctx.stack.journal(session)["events"])
    runs = [str(e.get("runId")) for e in events if e.get("type") == "run.admitted" and not (e.get("data") or {}).get("parentRunId")]
    return session, (runs[-1] if runs else (ctx.notes.get("live") or ("", ""))[1])


def cancel_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    if ctx.path.startswith("resume."):
        return resumed_cancel_turn(ctx, features)
    if ctx.path in ("surface.mcp", "surface.slack"):
        return elsewhere_stop_turn(ctx, features, "cancel")
    if ctx.path == "recovery":
        return recovered_cancel_turn(ctx, features)
    with HOLD_LOCK:
        t = start_held(ctx, f"never-{ctx.marker}")
        try:
            held = holding(ctx)
            if ctx.harness:
                # Today's loop: the chat's Stop, keyed by the stream's run id (no harness run, no journal).
                answer = ctx.api.request("POST", f"{t.base}/{t.session}/cancel", {"runId": t.run}) if t.run else None
            else:
                answer = ctx.api.request("POST", f"{t.base}/{t.session}/runs/{t.run}/cancel",
                                               {"idempotencyKey": f"journey-{secrets.token_hex(6)}"}) if t.run else None
        finally:
            release(ctx)
    status = ended(ctx, t.session, t.run) if t.run and not ctx.harness else "n/a"
    detail = (f"conversation {t.session} run {t.run or 'none'} held {held}, cancel "
              f"{answer.status if answer else 'not sent'}, run {status}")
    ok = held and answer is not None and answer.status < 300 and status == "cancelled"
    if ctx.harness:
        stopped = answer is not None and isinstance(answer.body, dict) and answer.body.get("cancelled") is True
        kept = f"never-{ctx.marker}" in saved_answers(ctx, f"{t.base}/{t.session}") if t.session else True
        ok = held and stopped and not kept
        detail += f", stop acknowledged {stopped}, the held answer saved anyway {kept}"
    return [(f, ok, detail) for f in features]


def delete_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """ADR-RUN-05: deleting the conversation while its run waits on the model stops the run, and it is gone."""
    if ctx.path in ("surface.mcp", "surface.slack"):
        return elsewhere_stop_turn(ctx, features, "delete")
    with HOLD_LOCK:
        t = start_held(ctx, f"never-{ctx.marker}")
        try:
            held = holding(ctx)
            gone = ctx.api.request("DELETE", f"{t.base}/{t.session}") if t.session else None
        finally:
            release(ctx)
    after = ctx.api.request("GET", f"{t.base}/{t.session}") if t.session else None
    status = ended(ctx, t.session, t.run) if t.run and not ctx.harness else "n/a"
    ok = (held and gone is not None and gone.status < 300 and after is not None and after.status in (403, 404, 410)
          and (ctx.harness is not None or status != "running"))
    detail = (f"conversation {t.session} run {t.run or 'none'} held {held}, delete {gone.status if gone else 'not sent'}, "
              f"read after {after.status if after else 'none'}, run {status}")
    return [(f, ok, detail) for f in features]


def conversation_of(ctx: Ctx, word: str, timeout: float = 60) -> str:
    """The person's conversation whose request holds ``word``, as their conversation list's search finds it: a run an
    MCP client started is not named to the client until it answers."""
    found: list[str] = []
    path = (f"{base_of(ctx)}?search={word}&sortBy=createdAt&sortOrder=desc&limit=5" if ctx.agent
            else f"/api/v1/conversations?search={word}&sortBy=createdAt&sortOrder=desc&limit=5")

    def listed() -> bool:
        rows = (ctx.api.ok("GET", path) or {}).get("conversations") or []
        found[:] = [str(r.get("_id") or r.get("id") or r.get("conversationId") or "") for r in rows]
        return bool(found and found[0])

    try:
        sh.wait_until(listed, timeout=timeout, what=f"the conversation holding {word}", every=2)
    except StackError:
        return ""
    return found[0]


def root_runs(ctx: Ctx, session: str) -> list[str]:
    events = list(ctx.stack.journal(session)["events"]) if session else []
    return [str(e.get("runId")) for e in events if e.get("type") == "run.admitted"
            and not (e.get("data") or {}).get("parentRunId")]


def cancel_seen(run: str, timeout: float = 60) -> bool:
    """Whether the worker running ``run`` logged that it saw the person's cancel (its ``run.cancel`` line, written when
    its cancel watcher finds ``run.cancel_requested`` in the journal, mid-call or between steps)."""
    try:
        sh.wait_until(lambda: any(" run.cancel " in line for line in sh.worker_lines(run)), timeout=timeout,
                      what="the worker to see the cancel", every=0.5)
        return True
    except StackError:
        return False


def elsewhere_stop_turn(ctx: Ctx, features: list[str], how: str) -> list[Line]:
    """R31JOURNEY-FU-2: a run an MCP client (pipeshub_chat) or a Slack thread started, stopped by its person in
    PipesHub while it waits on the model: Stop ends it cancelled, a delete ends it and the conversation is gone, and
    the held answer reaches neither the client nor the thread."""
    never = f"never-{ctx.marker}"
    text = script(ctx, ["hold"], never)
    box: dict[str, Any] = {}
    session = run = ""
    answer = None
    seen = False
    with HOLD_LOCK:
        ctx.stack.model.request("POST", "/__control/reset", {})
        try:
            if ctx.path == "surface.mcp":
                worker = threading.Thread(target=lambda: box.update(mcp=start_mcp(ctx, text)),
                                          daemon=True)
                worker.start()
                held = holding(ctx)
                session = conversation_of(ctx, ctx.marker)
                runs = root_runs(ctx, session)
                run = runs[-1] if runs else ""
            else:
                worker = None
                session, run, _sent = slack_start_thread(ctx, text)
                held = holding(ctx)
            if held and session and run:
                answer = (ctx.api.request("POST", f"{base_of(ctx)}/{session}/runs/{run}/cancel",
                                          {"idempotencyKey": f"journey-{secrets.token_hex(6)}"}) if how == "cancel"
                          else ctx.api.request("DELETE", f"{base_of(ctx)}/{session}"))
                if how == "cancel" and answer is not None and answer.status < 300:
                    # R50JFIX-7: the hold goes only once the worker has seen the cancel, so the run cannot answer
                    # before it is told; a held reply that ends while it reads the journal is the product's to drop.
                    seen = cancel_seen(run)
        finally:
            release(ctx)
        if worker is not None:
            worker.join(timeout=300)
    status = ended(ctx, session, run) if run else "none"
    after = ctx.api.request("GET", f"{base_of(ctx)}/{session}") if session and how == "delete" else None
    if ctx.path == "surface.mcp":
        told = json.dumps(box.get("mcp") or "not answered")
    else:
        time.sleep(5)
        told = json.dumps(slack_replies(ctx)) if "slack" in ctx.notes else "[]"
    leaked = never in told
    stopped = answer is not None and answer.status < 300
    if how == "cancel":
        ok = held and stopped and seen and status == "cancelled" and not leaked
    else:
        ok = (held and stopped and after is not None and after.status in (403, 404, 410) and status != "running"
              and not leaked)
    detail = (f"{ctx.path}: conversation {session or 'none'} run {run or 'none'} held {held}; {how} in PipesHub "
              f"{answer.status if answer else 'not sent'}" + (f", read after {after.status}" if after else "")
              + (f", the worker saw it before the hold ended {seen}" if how == "cancel" else "")
              + f"; run {status}; the held answer reached the {'client' if ctx.path == 'surface.mcp' else 'thread'} "
              f"{leaked} (want False): {told[:200]}")
    return [(f, ok, detail) for f in features]


def recovered_cancel_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R31JOURNEY-FU-2: a run whose worker and query service died while it waited on the model is driven on by a new
    attempt; its person stops it while that attempt waits on the model too, and it ends cancelled. The script holds
    twice: the first hold is spent by the attempt that was killed, the second holds the new one."""
    runtime = ctx.stack.runtime
    control = runtime.run_dir / "control"
    never = f"never-{ctx.marker}"
    answer = None
    with HOLD_LOCK:
        t = start_held(ctx, "", text=f"hold\nhold\nsay {never}")
        try:
            held = holding(ctx)
            started = {n: runtime.log(n).read_text(errors="replace").count(r) for n, r in ATTEMPT_HOLDERS.items()}
            for name in ATTEMPT_HOLDERS:
                (control / f"kill-{name}.done").unlink(missing_ok=True)
                (control / f"kill-{name}").touch()
            for name in ATTEMPT_HOLDERS:
                sh.wait_until(lambda n=name: (control / f"kill-{n}.done").exists(), timeout=60, what=f"{name} killed")
        finally:
            # R50JFIX-8: only a hold still waiting is let go; the killed attempt's may be gone with its connection.
            release_held(ctx)
        for name, ready in ATTEMPT_HOLDERS.items():
            sh.wait_until(lambda n=name, r=ready: runtime.log(n).read_text(errors="replace").count(r) > started[n],
                          timeout=300, what=f"{name} to start again")
        seen = False
        try:
            again = holding(ctx, timeout=240)
            if again and t.session and t.run:
                answer = ctx.api.request("POST", f"{t.base}/{t.session}/runs/{t.run}/cancel",
                                         {"idempotencyKey": f"journey-{secrets.token_hex(6)}"})
                # R50JFIX-7: the hold ends once the new attempt's worker has seen the cancel, as a person's Stop does.
                seen = answer.status < 300 and cancel_seen(t.run)
        finally:
            release(ctx)
    claims = sum(1 for line in sh.worker_lines(t.run) if " run.claimed " in line) if t.run else 0
    status = ended(ctx, t.session, t.run) if t.run else "none"
    kept = never in saved_answers(ctx, f"{t.base}/{t.session}") if t.session else False
    ok = (held and again and claims >= 2 and answer is not None and answer.status < 300 and seen
          and status == "cancelled" and not kept)
    detail = (f"conversation {t.session} run {t.run or 'none'} held {held}, the worker and query killed, claimed "
              f"{claims} times (want 2+), the new attempt held {again}; cancel {answer.status if answer else 'not sent'}, "
              f"seen by the worker before the hold ended {seen}; run {status}; the held answer saved anyway {kept}")
    return [(f, ok, detail) for f in features]


def budget_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    if not ctx.engine:
        return loop_budget_turn(ctx, features)
    name = f"journey-{ctx.marker}.md"
    searches = "\n".join(f'tool search_internal_knowledge {{"query": "{topic}"}} || knowledgegraph__search '
                         f'{{"query": "{topic}"}}' for topic in ("onboarding", "security", "billing"))
    text = f"Make a file {name} with the notes.\nusage {sh.CUT_SHORT_USAGE}\n{searches}\nsay {ctx.marker} complete"
    t = run_turn(ctx, text)
    ok, detail = sh.cut_short_shown(t.frames, sh.worker_lines(t.child) if t.child else [], name)
    engine = sh.child_engine(t.child) if t.child else ""
    return [(f, ok and engine == ctx.engine, f"conversation {t.session} run {t.run}, job on {engine or 'none'}: {detail}")
            for f in features]


def loop_budget_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R31JOURNEY-FU-6: a loop run saves a file, then a model call reports more usage than the run's budget holds; the
    run ends partial for its budget, says so with how to go on, and the saved file stays listed."""
    name = f"journey-{ctx.marker}.md"
    content = f"partial-{ctx.marker}"
    searches = [f'tool search_internal_knowledge {{"query": "{topic}"}} || knowledgegraph__search {{"query": "{topic}"}}'
                for topic in ("onboarding", "security")]
    # A model response that takes the run past its tokens (a loop's 2,000,000, policy loopMaxTokens) is not acted on.
    # A resume path's pause comes first (finding the tool, then the card: two calls), so its save is the third call
    # and the usage per call keeps three under the cap and the fourth over; elsewhere the save is the first call.
    usage = 600_000 if ctx.path.startswith("resume.") else sh.CUT_SHORT_USAGE * 5
    text = "\n".join([save_line(name, content), f"usage {usage}", *searches, f"say {ctx.marker} complete"])
    # On Slack the thread's last word is the partial answer, which says where it stopped.
    ctx.notes["expect"] = "spending limit"
    t = run_turn(ctx, text if ctx.mode != "deep" else f"{DEEP_LEAD}\n{text}")
    outcome = sh.shown_outcome(t.frames)
    finals = [e.get("data") or {} for e in t.events(ctx.stack)
              if e.get("type") == "x.pipeshub.answer.finalized" and e.get("runId") == t.run]
    cut = any(f.get("truncatedBy") == "budget" for f in finals) or outcome.get("reason") == "budget"
    partial = outcome.get("kind") == "partial" and outcome.get("reason") == "budget"
    said = partial or "spending limit" in json.dumps(finals) or "spending limit" in t.seen()
    # A deep run's coordinator plans before any sub-agent works, and the scripted usage is each call's: the cut comes
    # at its first call, before anything was made, so there the person is offered to go on ("continue" or "retry").
    # A job the default chat handed the rest to may save the file again as its partial deliverable (a new version).
    kept = (artifact_listed(ctx, t.session, name, None) if ctx.mode != "deep"
            else outcome.get("action") in ("continue", "retry"))
    ok = t.last in sh.TERMINAL and cut and said and kept
    named = ""
    if ctx.mode != "deep":
        # MR-17 (R36COVER-FU-5): the cut answer names the file it saved, each tool by a plain label, never its id.
        shown = sh.shown_answer(t.frames) + "\n" + " ".join(str(f.get("answer") or "") for f in finals)
        file_named, ids = cut_names(shown, name)
        ok = ok and file_named and not ids
        named = f"; the answer names the saved file {file_named}, tool ids shown {ids or 'none'} (want none)"
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; cut for its budget {cut}; the person told it "
              f"stopped at its spending limit and how to go on {said} (outcome {outcome.get('kind')}/"
              f"{outcome.get('reason')}, offered to {outcome.get('action')}); "
              + (f"the file saved before the cut listed {kept}" if ctx.mode != "deep"
                 else f"cut at the coordinator's first call, offered to go on {kept}") + named)
    return [(f, ok, detail) for f in features]


# The ids a cut answer once listed (R36COVER-FU-5) where a person reads a plain label.
RAW_TOOL_IDS = ("artifacts__save_artifact", "save_artifact", "knowledgegraph__search", "search_internal_knowledge")


def cut_names(shown: str, name: str) -> tuple[bool, list[str]]:
    """Whether a budget-cut answer names the saved file, and the raw tool ids it shows."""
    return name in shown, [i for i in RAW_TOOL_IDS if re.search(rf"(?<![\w/]){re.escape(i)}\b", shown)]


def listings(ctx: Ctx, server: str) -> list[float]:
    """When the scripted SaaS served each tools/list of one of its servers (fake_saas LISTINGS, epoch seconds)."""
    return [float(t) for t in ((ctx.stack.saas.ok("GET", "/__control/effects") or {}).get("listings") or {}).get(server, [])]


def claimed_at(run: str) -> float | None:
    """When a worker claimed the run (its first `run.claimed` log line), in epoch seconds."""
    return _logged_at(run, " run.claimed ")


def settled_at(run: str) -> float | None:
    """When the run's last attempt settled (its last `run.disposition` log line), in epoch seconds."""
    return _logged_at(run, " run.disposition ", last=True)


def _logged_at(run: str, marker: str, *, last: bool = False) -> float | None:
    lines = [line for line in sh.worker_lines(run) if marker in line]
    if not lines:
        return None
    from datetime import datetime

    stamp = datetime.strptime(lines[-1 if last else 0][:23], "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=UTC)
    return stamp.timestamp()


def listed_for_child(served: list[float], claimed: float | None, settled: float | None) -> int:
    """The server's listings made while the job ran, from its claim to its settling (R43MCP-FU-3). The parent resumes
    once the job settles (``resume_dispatch``): its own listings come after, and are not the job's."""
    if claimed is None:
        return 0
    return sum(1 for at in served if at >= claimed and (settled is None or at <= settled))


def pins_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R31-HOT2 (ADR-OWD-07 D-LAZY-5, R31HOT2-FU-1): the default chat uses a server held whole first, then hands off a
    code_task; the job inherits the parent's first-use pin. Proved: the parent's worker carried the pins and the child
    pinned them, the server was listed exactly once (the parent's), the job called a pinned tool, a tool outside the pin
    reached nothing, and the job's write was still held for the person (denied: nothing written). The parent read a
    private note first, so the flow guard holds the write ahead of the org's tool rule; both are the job's rules.

    The server is the person's for this journey alone (a new one, so no stored catalog stands in for the parent's
    listing), removed after; the journey runs alone in its group, as the default chat holds every server of theirs."""
    name = f"pins{ctx.marker}"
    url = f"{ctx.shape['owner']['serverUrl']}?server={name}"
    made = ctx.api.ok("POST", "/api/v1/mcp-servers/instances", {
        "name": name, "transport": "streamable_http", "authMode": "none", "url": url})
    instance = str(((made or {}).get("instance") or made or {}).get("_id") or
                   ((made or {}).get("instance") or made or {}).get("instanceId") or "")
    try:
        return _pins_turn(ctx, features, name)
    finally:
        if instance:
            ctx.api.request("DELETE", f"/api/v1/mcp-servers/instances/{instance}")


# The lazy surface's two ways to a server's tools (ADR-OWD-07 D-DISC-2, R43MCP-FU-4): a search by capability, or the
# server's toolset loaded by the name the prompt gives it. Either is a pass; a knowledge search is not one.
FIND_PATHS = ("search_tools", "fetch_tools")
KNOWLEDGE_TOOLS = ("search_internal_knowledge", "internal_exploration_agent", "knowledgegraph__", "retrieval__")


def find_read_verdict(frames: list[Any], read: str) -> tuple[str, bool, list[str]]:
    """How a turn reached ``read``: the first tool it called (the discovery), whether that call's result answered
    ``read``, and the knowledge tools it called. A model that searched knowledge alone (R36LIVE-FU-2's symptom) never
    makes the discovery, so the first call is a knowledge one and its result names no MCP tool."""
    names: dict[str, str] = {}
    for f in frames:
        if f.type == "TOOL_CALL_START":
            names[str(f.data.get("toolCallId") or "")] = str(f.data.get("toolCallName") or f.data.get("name") or "")
    first = next((f for f in frames if f.type == "TOOL_CALL_RESULT"), None)
    path = names.get(str(first.data.get("toolCallId") or ""), "") if first is not None else ""
    answered = first is not None and path in FIND_PATHS and read in json.dumps(first.data)
    knowledge = [n for n in names.values() if n.startswith(KNOWLEDGE_TOOLS)]
    return path, answered, knowledge


def after_pause(frames: list[Any]) -> list[Any]:
    """A resume path's frames after its pause (the first card or question the run stopped at), less the results of
    calls made before it (the paused call's own result arrives once it is answered): the probe's calls alone
    (R43MCP-FU-1). All of them when the run never paused."""
    at = next((n for n, f in enumerate(frames) if card(f) or (f.type == "CUSTOM" and "question" in str(f.name))), None)
    if at is None:
        return frames
    rest = frames[at + 1:]
    started = {str(f.data.get("toolCallId") or "") for f in rest if f.type == "TOOL_CALL_START"}
    return [f for f in rest if f.type != "TOOL_CALL_RESULT" or str(f.data.get("toolCallId") or "") in started]


def job_find_read_verdict(events: list[Json], child: str, read: str) -> tuple[str, bool, list[str]]:
    """A job's way to ``read``, from its own journal (its gateway calls, ``tool.intent``): the first tool it called,
    whether it called ``read``, and the knowledge tools it called. The engine's ``search_tools`` is answered by the Tool
    Gateway itself and journals no intent (R50JFIX-9: a discovery reaches nothing and has no effect to record), so the
    discovery shows as the read reached with no knowledge tool before it, and the server listed exactly once (the
    scripted MCP's own count, ``_find_read``)."""
    names = [str((e.get("data") or {}).get("toolId") or (e.get("data") or {}).get("toolName") or "") for e in events
             if e.get("type") == "tool.intent" and e.get("runId") == child]
    first = names[0] if names else ""
    called = any(n.endswith((read, READ_TOOL)) for n in names)
    knowledge = [n for n in names if any(k in n for k in KNOWLEDGE_TOOLS)]
    return first, called, knowledge


def find_read_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R36LIVE-FU-2: the default chat reads a note held by a server the person attached without a description and has
    never used. Both ways the lazy surface offers are walked, each on a server of its own (R43MCP-FU-4): a search by
    capability words alone (neither the server's name nor the tool's), as a model would when no server fits by name,
    and ``fetch_tools`` on the toolset the prompt names, the path gpt-6-luna took by hand (R43-LIVE, 2/2). Before, such
    a server was in no line of the prompt and no search listed it, so only knowledge was searched. Proved for each:
    the discovery answered the server's lookup_note, no knowledge tool was called, the server was listed exactly once
    (the discovery's first use), and the note reached the model.

    The servers are the person's for this journey alone (new ones, so nothing is stored of them), removed after; the
    journey runs alone in its group, as the default chat holds every server of theirs."""
    oks: list[bool] = []
    details: list[str] = []
    # A job (R43MCP-FU-1) searches with the engine's search_tools over the same LazyMcpServers.warm and source list;
    # fetch_tools is the loop's.
    for how in FIND_PATHS[:1] if ctx.engine else FIND_PATHS:
        # A marker of each walk's own (R50JFIX-10): a resume path's pause writes a ticket named by it, counted once.
        ok, detail = _find_read(replace(ctx, marker=f"{ctx.marker}{how[0]}"), how)
        oks.append(ok)
        details.append(detail)
    return [(f, all(oks), "; ".join(details)) for f in features]


def _find_read(ctx: Ctx, how: str) -> tuple[bool, str]:
    name = f"find{how[0]}{ctx.marker}"
    url = f"{ctx.shape['owner']['serverUrl']}?server={name}"
    made = ctx.api.ok("POST", "/api/v1/mcp-servers/instances", {
        "name": name, "transport": "streamable_http", "authMode": "none", "url": url})
    instance = str(((made or {}).get("instance") or made or {}).get("_id") or
                   ((made or {}).get("instance") or made or {}).get("instanceId") or "")
    try:
        read, topic, say = f"mcp_{name}_{READ_TOOL}", f"find-{how[0]}-{ctx.marker}", f"found-{how[0]}-{ctx.marker}"
        find = ({"query": "look up private note", "make_visible": True} if how == "search_tools"
                else {"toolset": f"mcp_{name}"})
        t = run_turn(ctx, "\n".join([
            f"tool {how} {json.dumps(find)}",
            f"tool {read} {json.dumps({'topic': topic})}",
            f"say {say}",
        ]))
        if ctx.engine:
            path, answered, knowledge = job_find_read_verdict(t.events(ctx.stack), t.child, read)
        else:
            path, answered, knowledge = find_read_verdict(
                after_pause(t.frames) if ctx.path.startswith("resume.") else t.frames, read)
        note = NOTE.format(topic=topic) in t.seen()
        # The server is this journey's alone, so every listing the scripted MCP served is the turn's: exactly one, its
        # first use, whether the parent's grounding search or the job's discovery made it (R50JFIX-9).
        served = len(listings(ctx, name))
        child_ok, child_detail = child_check(ctx, t)
        ok = (t.last == "RUN_FINISHED" and say in sh.answer(t.frames) and answered and not knowledge and note
              and served == 1 and child_ok)
        return ok, (f"[{how}] conversation {t.session} run {t.run} ended {t.last}; {child_detail}; the discovery "
                    f"({path or 'none'}) "
                    f"answered {read}: {answered}; knowledge tools called {knowledge or 'none'} (want none); the note "
                    f"reached the model: {note}; {name} listed {served} time(s) (want 1)")
    finally:
        if instance:
            ctx.api.request("DELETE", f"/api/v1/mcp-servers/instances/{instance}")


def _pins_turn(ctx: Ctx, features: list[str], server: str) -> list[Line]:
    read = f"mcp_{server}_{READ_TOOL}"
    parent_topic, child_topic = f"parent-{ctx.marker}", f"child-{ctx.marker}"
    write = {"title": f"journey pinned {ctx.marker}", "body": "journey"}
    outside = f"mcp_{server}_not_listed_{ctx.marker}"
    say = f"pinned-{ctx.marker}"
    text = "\n".join([
        f"handoff-first search_tools {json.dumps({'query': read, 'make_visible': True})}",
        f"handoff-first {read} {json.dumps({'topic': parent_topic})}",
        f"tool {read} {json.dumps({'topic': child_topic})}",
        f"tool mcp_{server}_{ASK_TOOL} {json.dumps(write)}",
        f"tool {outside} {json.dumps({'topic': 'outside'})}",
        f"say {say}",
    ])
    t = run_turn(ctx, text, decide="denied")
    served = listings(ctx, server)
    child_from = claimed_at(t.child) if t.child else None
    by_child = listed_for_child(served, child_from, settled_at(t.child) if t.child else None)
    seen = t.seen()
    parent_read, child_read = (NOTE.format(topic=x) in seen for x in (parent_topic, child_topic))
    carried = next((line for line in sh.worker_lines(t.run) if "child.mcp_pins_carried" in line), "")
    pinned = [m for m in ((t.spec(ctx.stack, t.child).get("extensions") or {}).get("x.pipeshub") or {}).get("mcp") or []
              if m.get("pins")] if t.child else []
    cards = cards_of(t)
    rule = rule_of(cards[0]) if cards else ""
    effects = effects_of(ctx, write)
    reached = f"not_listed_{ctx.marker}" in json.dumps(sh.saas(ctx.stack, "reads"))
    child_ok, child_detail = child_check(ctx, t)
    ok = (child_ok and parent_read and bool(carried) and bool(pinned) and len(served) >= 1 and child_from is not None
          and by_child == 0 and child_read
          and bool(cards) and effects == 0 and not reached)
    detail = (f"conversation {t.session} run {t.run} ended {t.last}, child {t.child or 'none'}; {child_detail}; parent "
              f"read first {parent_read}; pins carried {bool(carried)}, child pinned servers {len(pinned)}; tools/list "
              f"of {server} served {len(served)}, {by_child} while the job ran (want 0); the job's pinned call answered {child_read}; a rule "
              f"held the job's write {bool(cards)} ({rule or 'none'}), written {effects}; the tool outside the pin "
              f"reached the server {reached}")
    return [(f, ok, detail) for f in features]


# recovery: every process that may hold the run's attempt (the worker, and the query service that claims a turn
# inline on this stack) is killed while the run waits on the model; each starts again and a new attempt drives the run
# on from its journal (test_mcplazy_first_use._crash_attempt, DE-5).
ATTEMPT_HOLDERS = {"worker": "worker.started", "query": "Application startup complete"}


def recovered_turn(ctx: Ctx, text: str, decisions: list[Decision]) -> Turn:
    runtime = ctx.stack.runtime
    control = runtime.run_dir / "control"
    unsupervised = [n for n in ATTEMPT_HOLDERS if not (runtime.run_dir / "logs" / f"{n}-supervisor.log").exists()]
    if unsupervised:
        raise StackError(f"{unsupervised} not under the stack's supervisor (run.sh human-up sets E2E_SUPERVISE_QUERY=1)")
    with HOLD_LOCK:
        held_turn = start_held(ctx, "", text=f"hold\n{text}")
        try:
            held = holding(ctx)
            started = {n: runtime.log(n).read_text(errors="replace").count(r) for n, r in ATTEMPT_HOLDERS.items()}
            for name in ATTEMPT_HOLDERS:
                (control / f"kill-{name}.done").unlink(missing_ok=True)
                (control / f"kill-{name}").touch()
            for name in ATTEMPT_HOLDERS:
                sh.wait_until(lambda n=name: (control / f"kill-{n}.done").exists(), timeout=60, what=f"{name} killed")
        finally:
            # R50JFIX-8: only a hold still waiting is let go; the killed attempt's may be gone with its connection.
            release_held(ctx)
        for name, ready in ATTEMPT_HOLDERS.items():
            sh.wait_until(lambda n=name, r=ready: runtime.log(n).read_text(errors="replace").count(r) > started[n],
                          timeout=300, what=f"{name} to start again")
    t = follow(ctx, held_turn.session, held_turn.run, [], decisions)
    claims = sum(1 for line in sh.worker_lines(t.run) if " run.claimed " in line)
    ctx.notes["resumed"] = held and claims >= 2
    ctx.notes["started"] = f"held {held}, the run claimed {claims} times (a new attempt after the kill)"
    return t


# MR-7 (ADR-CAP-01, R31UIFU-FU-1): Node just restarted and the broker's health cannot answer its probe (the broker
# paused: connections are accepted and never answered, as a broker still starting does), so the first rounds are
# unknown. A deep-mode job admitted then must still get its own sandbox once the broker answers.
SANDBOX_CODE = {"language": "python", "code": "print('sandbox-' + 'ok')"}
SANDBOX_WAITING = ("run.agent_code_pending", "run.claim_deferred")


def _docker(*argv: str) -> None:
    subprocess.run(["docker", *argv], check=True, capture_output=True, text=True, timeout=60)


def _restart_node(stack: Any) -> int:
    """Restarts Node through the stack's supervisor; the offset of the new process's log."""
    runtime = stack.runtime
    control, log = runtime.run_dir / "control", runtime.log("node")
    ready = "Server started on port"
    before = log.read_text(errors="replace").count(ready)
    offset = len(log.read_text(errors="replace"))
    (control / "restart-node.done").unlink(missing_ok=True)
    (control / "restart-node").touch()
    sh.wait_until(lambda: (control / "restart-node.done").exists(), timeout=120, what="Node to stop")
    sh.wait_until(lambda: log.read_text(errors="replace").count(ready) > before, timeout=300, what="Node to start")
    return offset


def code_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """A loop run's own code sandbox on a settled deployment: run_code runs and the run pinned the agent-code profile."""
    say = f"done-{ctx.marker}"
    t = run_turn(ctx, script(ctx, [f"tool run_code {json.dumps(SANDBOX_CODE)} || coding_agent "
                                   + json.dumps({"goal": f"Run it.\ntool run_code {json.dumps(SANDBOX_CODE)}\nsay ran"})],
                             say))
    profile = (t.spec(ctx.stack).get("grants") or {}).get("sandboxProfile")
    ran = "sandbox-ok" in t.seen()
    child_ok, child_detail = child_check(ctx, t)
    ok = t.last == "RUN_FINISHED" and profile == "agent-code" and ran and child_ok
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; pinned sandboxProfile {profile} "
              f"(want agent-code); run_code output seen {ran}")
    return [(f, ok, detail) for f in features]


def sandbox_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    if ctx.path != "agent.deep":
        return code_turn(ctx, features)
    stack = ctx.stack
    broker = stack.runtime.container("sandbox-broker")
    log = stack.runtime.log("node")
    say = f"done-{ctx.marker}"
    text = script(ctx, [f"tool run_code {json.dumps(SANDBOX_CODE)} || coding_agent "
                        + json.dumps({"goal": f"Run it.\ntool run_code {json.dumps(SANDBOX_CODE)}\nsay ran"})], say)
    box: dict[str, Any] = {}
    paused = False
    try:
        _docker("pause", broker)
        paused = True
        offset = _restart_node(stack)
        settling = sh.capabilities_state(stack)
        worker = threading.Thread(target=lambda: box.update(turn=run_turn(ctx, text)), daemon=True)
        worker.start()
        sh.wait_until(lambda: any(e in log.read_text(errors="replace")[offset:] for e in SANDBOX_WAITING)
                      or not worker.is_alive(), timeout=120, what="the job to be admitted while the broker is silent")
        waited = [e for e in SANDBOX_WAITING if e in log.read_text(errors="replace")[offset:]]
        _docker("unpause", broker)
        paused = False
        worker.join(timeout=300)
    finally:
        if paused:
            _docker("unpause", broker)
    sh.wait_capabilities_settled(stack)
    t: Turn | None = box.get("turn")
    if t is None:
        return [(f, False, f"the turn did not finish (capabilities {settling} at admission)") for f in features]
    profile = (t.spec(stack).get("grants") or {}).get("sandboxProfile")
    ran = "sandbox-ok" in t.seen()
    ok = t.last == "RUN_FINISHED" and settling == "settling" and bool(waited) and profile == "agent-code" and ran
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; capabilities {settling} when admitted, Node logged "
              f"{waited or 'nothing'}; pinned sandboxProfile {profile} (want agent-code); run_code output seen {ran}")
    return [(f, ok, detail) for f in features]


# R31GVISO-FU-3 (R32-SECCOMP): a job's command that grows without bound meets its sandbox's memory limit (oom_lane.py's
# balloon). On gVisor the whole sandbox ends: the job fails once as out_of_memory with its limit, never driven again,
# and the person's stream ends the job with that code. On runc the kernel may kill the command alone: the engine reads
# its exit and goes on, and nothing reads as an out-of-memory end.
BALLOON = "python3 -c \"b=[]; [b.append(b'x'*(64<<20)) for _ in iter(int, 1)]\""


OOM_WORDS = ("out_of_memory", "out of memory", "memory limit", "MemoryError")


def loop_oom_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """A loop run's run_code meets its sandbox's memory limit: the result names it to the person and the run goes on
    to its answer."""
    say = f"survived-{ctx.marker}"
    code = {"language": "python", "code": "b = []\nwhile True:\n    b.append(b'x' * (64 << 20))"}
    # A deep run's sub-agent runs code itself or through its coding helper (as code_turn's).
    line = (f"tool run_code {json.dumps(code)} || coding_agent "
            + json.dumps({"goal": f"Run it.\ntool run_code {json.dumps(code)}\nsay ran"}))
    t = run_turn(ctx, script(ctx, [line], say))
    results = [f.data for f in t.frames if f.type == "TOOL_CALL_RESULT"]
    named = any(word in json.dumps(results) or word in t.seen() for word in OOM_WORDS)
    answered = t.last == "RUN_FINISHED" and say in sh.answer(t.frames)
    shown = [str(r.get("content") or "")[:120] for r in results]
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; run_code's result names the memory limit {named} "
              f"({shown}); answered after it {answered}")
    return [(f, named and answered, detail) for f in features]


def oom_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    if not ctx.engine:
        return loop_oom_turn(ctx, features)
    say = f"survived-{ctx.marker}"
    t = run_turn(ctx, script(ctx, [f"shell {BALLOON}"], say))
    lines = sh.worker_lines(t.child) if t.child else []
    oom = [x for x in lines if "engine.sandbox.out_of_memory" in x]
    failed = [x for x in lines if "engine.kernel.failed" in x and "reasonCode=out_of_memory" in x]
    released = [x for x in lines if "engine.kernel.release" in x]
    errors = [f.data for f in t.frames if f.type == "RUN_ERROR" and f.data.get("runId") == t.child]
    shown = any(e.get("errorType") == "out_of_memory" for e in errors)
    limit = oom[0].split("memoryMb=", 1)[1].split()[0] if oom else ""
    gvisor = (ctx.stack.env.get("E2E_SANDBOX_RUNTIME") == "runsc") or any("mode=gvisor" in x for x in lines)
    child_ok, child_detail = child_check(ctx, t)
    ended_ok = t.last in sh.TERMINAL
    if oom or gvisor:
        ok = child_ok and bool(oom) and len(failed) == 1 and not released and shown and bool(limit) and ended_ok
    else:
        ok = child_ok and not failed and ended_ok
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; gvisor {gvisor}; the sandbox's "
              f"limit ended it {bool(oom)} (limit {limit or 'none'} MB); failed out_of_memory {len(failed)} time(s), "
              f"driven again {len(released)}; the person's stream ended the job with out_of_memory {shown}")
    return [(f, ok, detail) for f in features]


# R32-REPO (ADR-REPO-01, ADR-RUN-03 D5): a job asked to check out a public repository. The stack's stand-in git host
# answers as github.com (docker-compose.fakegit.yml); every pxh-e2e/<name> is its journey repository, whose README
# holds REPO_MARKER, and each request's path lands in <run dir>/fake-git/requests.log.
REPO_MARKER = "pxh-journey-repo-marker-7f3a"
# capability_limits.py's words: what the job's step and the parent's tool result say of a clone that did not happen.
DECLINED_LINE = "the person declined the clone"


def git_requests(ctx: Ctx, name: str) -> list[str]:
    log = ctx.stack.runtime.run_dir / "fake-git" / "requests.log"
    return [line for line in (log.read_text().splitlines() if log.exists() else []) if f"/pxh-e2e/{name}.git/" in line]


@contextmanager
def seeded_web(ctx: Ctx) -> Iterator[None]:
    """A saved agent reaches public code by default only with web search on (ADR-REPO-01): the seeded coder agent has
    none, so its journey turns it on, as its owner would, and puts it back."""
    if ctx.shape_id != "seeded" or ctx.agent is None:
        yield
        return
    stored = (ctx.stack.api.ok("GET", f"/api/v1/agents/{ctx.agent}") or {}).get("agent") or {}
    before = stored.get("webSearch")
    ctx.stack.api.ok("PUT", f"/api/v1/agents/{ctx.agent}", {"webSearch": {"provider": WEB_PROVIDER}})
    try:
        yield
    finally:
        ctx.stack.api.request("PUT", f"/api/v1/agents/{ctx.agent}", {"webSearch": before})


def repo_turn(ctx: Ctx, features: list[str], decide: str | None) -> tuple[Turn, str, str]:
    name = f"journey-{ctx.marker}"
    lines = [f"shell ph git clone https://github.com/pxh-e2e/{name}", f"shell cat {name}/README.md"]
    say = f"repo-{ctx.marker}"
    with seeded_web(ctx):
        t = run_turn(ctx, script(ctx, lines, say), decide=decide)
    return t, name, say


def answer_limits(events: list[Json], run: str) -> list[str]:
    """The kinds of what the run's answer says it could not do (R32REPO-FU-1: ``limits`` on its
    ``x.pipeshub.answer.finalized``), from the journal."""
    answers = [e.get("data") or {} for e in events if e.get("type") == "x.pipeshub.answer.finalized" and e.get("runId") == run]
    return [str(i.get("kind")) for a in answers for i in ((a.get("limits") or {}).get("items") or [])]


def _limits_said(ctx: Ctx, t: Turn, words: str) -> bool:
    """The job's limits reached the parent: its step's line or its tool result, in the stream or the journal."""
    return words in t.seen() or words in json.dumps(t.events(ctx.stack))


def repo_allow_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    t, name, say = repo_turn(ctx, features, "approved")
    cards = cards_of(t)
    named = any(f"pxh-e2e/{name}" in json.dumps(c) for c in cards)
    fetched = any(line.startswith("POST ") and line.endswith("git-upload-pack") for line in git_requests(ctx, name))
    read = REPO_MARKER in t.seen()
    lost = not read and lost_output(ctx, t, f"cat {name}/README.md")
    child_ok, child_detail = child_check(ctx, t)
    answered = t.last == "RUN_FINISHED" and say in sh.answer(t.frames)
    ok = child_ok and named and fetched and (read or lost) and answered
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; card names the repository {named} "
              f"({len(cards)} cards); the git host served the clone {fetched}; the README's marker read in the job "
              f"{OUTPUT_LOST if lost else read}; answered {answered}")
    return [(f, ok, detail) for f in features]


def repo_deny_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    t, name, _ = repo_turn(ctx, features, "denied")
    cards = cards_of(t)
    served = git_requests(ctx, name)
    said = _limits_said(ctx, t, DECLINED_LINE)
    answer = answer_limits(t.events(ctx.stack), t.run)
    read = REPO_MARKER in t.seen()
    child_ok, child_detail = child_check(ctx, t)
    ok = (child_ok and bool(cards) and not served and said and "repository.declined" in answer and not read
          and t.last == "RUN_FINISHED")
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; cards {len(cards)}; the git host "
              f"saw {len(served)} requests (want 0); the job's limits say {DECLINED_LINE!r}: {said}; the answer's "
              f"could-not-do line {answer}; marker read {read}")
    return [(f, ok, detail) for f in features]


def named_repo_turn(ctx: Ctx, lines: list[str], decide: str | None) -> tuple[Turn, str, str]:
    """R41-CLONE: a request that names a repository on the git host, as a person would write it; ``lines``
    follow, with ``{name}`` for the repository's folder."""
    name = f"journey-{ctx.marker}"
    say = f"repo-{ctx.marker}"
    asked = f"Check the code of the GitHub repository github.com/pxh-e2e/{name} and tell me what its README says."
    with seeded_web(ctx):
        t = run_turn(ctx, script(ctx, [asked, *(line.format(name=name) for line in lines)], say), decide=decide)
    return t, name, say


def repo_named_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """The job is handed the named repository as its clone command and clones it (the scripted model runs the commands
    its task gives: ``clones``), then reads it; nothing is said to be unread."""
    t, name, say = named_repo_turn(ctx, ["clones", "shell cat {name}/README.md"], "approved")
    fetched = any(line.startswith("POST ") and line.endswith("git-upload-pack") for line in git_requests(ctx, name))
    read = REPO_MARKER in t.seen()
    lost = not read and lost_output(ctx, t, f"cat {name}/README.md")
    answer = answer_limits(t.events(ctx.stack), t.run)
    child_ok, child_detail = child_check(ctx, t)
    answered = t.last == "RUN_FINISHED" and say in sh.answer(t.frames)
    ok = child_ok and fetched and (read or lost) and answered and "repository.not_cloned" not in answer
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; the git host served the clone "
              f"{fetched}; the README's marker read {OUTPUT_LOST if lost else read}; the answer's could-not-do line "
              f"{answer}; answered {answered}")
    return [(f, ok, detail) for f in features]


def repo_unread_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """A job that never clones the repository its task names: the answer's could-not-do line says so, from the journal."""
    t, name, _ = named_repo_turn(ctx, [], None)
    served = git_requests(ctx, name)
    answer = answer_limits(t.events(ctx.stack), t.run)
    child_ok, child_detail = child_check(ctx, t)
    ok = child_ok and not served and "repository.not_cloned" in answer and t.last == "RUN_FINISHED"
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; the git host saw {len(served)} "
              f"requests (want 0); the answer's could-not-do line {answer} (want repository.not_cloned)")
    return [(f, ok, detail) for f in features]


# Web search: the org's default provider and each seeded agent's are the scripted SearXNG for the whole run
# (scripted_web_search), so no journey reaches a live search engine; its results name WEB_RESULT.
WEB_SEARCH = "/api/v1/configurationManager/web-search"
WEB_PROVIDER = "searxng"
WEB_RESULT = "handbook.example/expenses"


def web_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    say = f"web-{ctx.marker}"
    before = sum(1 for r in sh.saas(ctx.stack, "reads") if r.get("tool") == "searxng.search")
    t = run_turn(ctx, script(ctx, [f"tool web_search {json.dumps({'query': f'expenses policy {ctx.marker}'})}"], say))
    searched = sum(1 for r in sh.saas(ctx.stack, "reads") if r.get("tool") == "searxng.search") - before
    called = [f for f in t.frames if f.type == "TOOL_CALL_START" and "web_search" in str(f.data.get("toolCallName") or
                                                                                        f.data.get("name") or "")]
    seen = WEB_RESULT in t.seen()
    answered = t.last == "RUN_FINISHED" and say in sh.answer(t.frames)
    child_ok, child_detail = child_check(ctx, t)
    # A job's calls reach the person as its steps, not as the parent's tool calls: the result in the stream holds both.
    ok = child_ok and seen and searched >= 1 and answered and (bool(called) or bool(ctx.engine) or bool(ctx.harness))
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; web_search called "
              f"{len(called)} time(s) in the stream; the scripted SearXNG searched {searched}; its result seen {seen}; "
              f"answered after it {answered}")
    return [(f, ok, detail) for f in features]


@contextmanager
def scripted_web_search(stack: Any) -> Iterator[None]:
    """The org's default web search and the seeded agents' set to the scripted SearXNG for the run, put back after."""
    view = stack.api.ok("GET", WEB_SEARCH) or {}
    before = [p for p in view.get("providers") or [] if p.get("providerKey")]
    default = next((p.get("providerKey") for p in before if p.get("isDefault")), None)
    mine = next((p for p in before if p.get("provider") == WEB_PROVIDER), None)
    url = f"{stack.env.get('E2E_FAKE_SAAS_URL') or 'http://pxh-fake-saas:8090'}/searxng"
    body = {"provider": WEB_PROVIDER, "configuration": {"baseUrl": url}, "isDefault": True}
    if mine is None:
        stack.api.ok("POST", f"{WEB_SEARCH}/providers", body)
    else:
        stack.api.ok("PUT", f"{WEB_SEARCH}/providers/{mine['providerKey']}", body)
    key = next((p.get("providerKey") for p in (stack.api.ok("GET", WEB_SEARCH) or {}).get("providers") or []
                if p.get("provider") == WEB_PROVIDER), None)
    agents = {k: ((stack.api.ok("GET", f"/api/v1/agents/{stack.seed[k]}") or {}).get("agent") or {}).get("webSearch")
              for k in sh.AGENTS}
    for k in agents:
        stack.api.ok("PUT", f"/api/v1/agents/{stack.seed[k]}", {"webSearch": {"provider": WEB_PROVIDER}})
    try:
        yield
    finally:
        for k, value in agents.items():
            stack.api.request("PUT", f"/api/v1/agents/{stack.seed[k]}", {"webSearch": value})
        if mine is None and key:
            stack.api.request("DELETE", f"{WEB_SEARCH}/providers/{key}")
        elif mine is not None:
            stack.api.request("PUT", f"{WEB_SEARCH}/providers/{mine['providerKey']}",
                              {k: mine[k] for k in ("provider", "configuration", "isDefault") if k in mine})
        if default and default != key:
            stack.api.request("PUT", f"{WEB_SEARCH}/default/{default}")


# SEC-B-02: the scripted SaaS's public lookup declares readOnlyHint alone, as most third-party tools do; what it is
# asked reaches its operator, so it is an outside destination (fake_saas/server.py web_lookup).
OPEN_WORLD_TOOL = "web_lookup"


def open_world_reached(ctx: Ctx, query: str) -> int:
    digest = hashlib.sha256(json.dumps({"query": query}, sort_keys=True).encode()).hexdigest()[:16]
    return sum(1 for r in sh.saas(ctx.stack, "reads") if r.get("tool") == OPEN_WORLD_TOOL and r.get("argsDigest") == digest)


def flow_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """The flow guard: a private read (the MCP note, provenance nobody can name) and then an external write; the
    write is held by the flow rule ahead of the org's tool rule, the person denies it, and nothing is written.

    Where the person holds the scripted SaaS whole (the owner shapes), the run first looks the note's words up with
    the server's open-world read (SEC-B-02: readOnlyHint without openWorldHint: false is an outside destination): the
    flow rule holds that too, and the denied lookup never reaches the server."""
    topic = f"flow-{ctx.marker}"
    args = {"title": f"journey flow {ctx.marker}", "body": "journey"}
    query = f"finance review {topic}"
    lookup: list[str] = []
    if ctx.owner:
        name = f"mcp_{sh.MCP_NAME}_{OPEN_WORLD_TOOL}"
        line = f"tool {name} {json.dumps({'query': query})}"
        lookup = [line] if ctx.engine else [sh.discover(name), line]
    t = run_turn(ctx, script(ctx, [*ctx.call(ctx.read_tool(), {"topic": topic}), *lookup,
                                   *ctx.call(ctx.ask_tool(), args)], f"after-{ctx.marker}"), decide="denied")
    cards = cards_of(t)
    rules = [rule_of(c) for c in cards]
    rule = rules[-1] if rules else ""
    read = NOTE.format(topic=topic) in t.seen()
    effects = effects_of(ctx, args)
    child_ok, child_detail = child_check(ctx, t)
    ok = child_ok and read and "flow" in rule and effects == 0 and t.last in sh.TERMINAL
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; the private read reached the person "
              f"{read}; the write's card {rule or 'none'} ({len(cards)} cards); written {effects} (want 0)")
    if lookup:
        asked = [r for c, r in zip(cards, rules, strict=True) if OPEN_WORLD_TOOL in json.dumps(c)]
        reached = open_world_reached(ctx, query)
        ok = ok and len(cards) == 2 and bool(asked) and "flow" in asked[0] and reached == 0
        detail += (f"; the open-world lookup after it held by {asked[0] if asked else 'nothing'} (SEC-B-02), "
                   f"reached the server {reached} time(s) (want 0)")
    if ctx.harness:
        # Today's loop cannot hold a call for a person (X-CLASSIC): the write after the private read is refused.
        ok = read and effects == 0 and t.last in sh.TERMINAL
    return [(f, ok, detail) for f in features]


# Attachments and memories are proved by their words reaching the model: the scripted model's canaries (digests
# only), one per journey, set for the run at its start (watched_words), since setting them replaces the set.
def canary(journey: str, kind: str) -> str:
    return f"pxhj-{kind}-{hashlib.sha256(journey.encode()).hexdigest()[:14]}"


def canary_hits(ctx: Ctx, word: str) -> int:
    digest = hashlib.sha256(word.encode()).hexdigest()[:16]
    return sum(1 for h in (ctx.stack.model.ok("GET", "/__control/canaries") or {}).get("hits") or []
               if h.get("canaryDigest") == digest)


@contextmanager
def watched_words(stack: Any, journeys: list[Json]) -> Iterator[None]:
    words = [canary(j["id"], kind) for j in journeys
             for kind, turn in (("file", "attach"), ("memory", "memory"), ("memoryweb", "memory")) if j["turn"] == turn]
    if words:
        stack.model.ok("POST", "/__control/canaries", {"values": words})
    try:
        yield
    finally:
        if words:
            stack.model.ok("POST", "/__control/canaries", {"values": []})


def upload(ctx: Ctx, filename: str, content: bytes) -> Json:
    import urllib.error
    import urllib.request

    from lane import multipart

    body, content_type = multipart("files", filename, content, "text/plain")
    request = urllib.request.Request(f"{ctx.api.base}{base_of(ctx)}/attachments/upload", data=body, method="POST")
    request.add_header("Content-Type", content_type)
    request.add_header("Authorization", f"Bearer {ctx.api.token()}")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return dict(json.loads(response.read())["attachments"][0])
    except urllib.error.HTTPError as error:
        raise StackError(f"upload -> {error.code}: {error.read()[:300]!r}") from None


def attach_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """A file attached to the turn reaches the model that answers it: the loop's own, or the job's it hands off to
    (where the file is the job's input too: the job reads it back)."""
    word = canary(ctx.notes["journey"], "file")
    name = f"journey-{ctx.marker}.txt"
    ref = upload(ctx, name, f"Attached notes: the code word is {word}.\n".encode())
    # R35SLACK-FU-2: beside it, a file another person uploaded, which this person may not read. The run that reads the
    # turn's files (the loop, or the default chat or agent that hands work to a job: a job is given only the files its
    # parent could read) names it in the answer, once, through the shared finaliser (ADR-RUN-03 D5).
    theirs = f"theirs-{ctx.marker}.txt"
    refs = [ref] if ctx.harness else [ref, others_upload(ctx, theirs, b"Someone else's notes.\n")]
    before = canary_hits(ctx, word)
    say = f"attached-{ctx.marker}"
    lines = ["shell cat /pipeshub/inputs/* 2>/dev/null || ls -la /pipeshub/inputs"] if ctx.engine else []
    body = body_of(ctx, script(ctx, lines, say), attachments=refs)
    t = converse(ctx, f"{base_of(ctx)}/stream", body, [])
    hits = canary_hits(ctx, word) - before
    answered = t.last == "RUN_FINISHED" and say in sh.answer(t.frames)
    child_ok, child_detail = child_check(ctx, t)
    ok = child_ok and answered and hits >= 1
    detail = f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; the file's words reached the model {hits} time(s)"
    if not ctx.harness:
        finals = [e.get("data") or {} for e in t.events(ctx.stack)
                  if e.get("type") == "x.pipeshub.answer.finalized" and e.get("runId") == t.run]
        answer = str(finals[-1].get("answer") or "") if finals else ""
        named = [i.get("kind") for f in finals for i in ((f.get("limits") or {}).get("items") or [])
                 if i.get("file") == theirs]
        once = named == ["attachment.not_accessible"] and answer.count(theirs) == 1
        ok = ok and once
        detail += f"; the other person's file named in the answer once as not accessible {once} ({named})"
    if ctx.engine:
        read = word in "".join(str(v.get("text") or "") for v in sh.child_steps(t.frames, t.child)) if t.child else False
        brief = next((e for e in t.events(ctx.stack) if e.get("type") == "run.spec_pinned" and e.get("runId") == t.child), {})
        withheld = "not_handed_off" in json.dumps(brief)
        ok = ok and read
        detail += f"; the job read the file {read}; the job's brief withholds the chat's files {withheld}"
    if (ctx.path, ctx.shape_id) in OVER_CAP_CELLS:
        capped, said = over_cap_turn(ctx)
        ok, detail = ok and capped, f"{detail}; {said}"
    return [(f, ok, f"{detail}; answered {answered}") for f in features]


# MR-16 (R36COVER-FU-3, R46FIX-2): the per-input cap lowered for the services while the journey runs, so a file the
# job made is over it without moving 100 MB. Alone in its group (ALONE_CELLS): the services restart with it.
OVER_CAP_CELLS = frozenset({("job.opencode", "seeded")})
INPUT_CAP_ENV = {"HARNESS_INPUT_MAX_MB": "1"}
OVER_CAP_BYTES = 2_200_000


def presented_artifacts(events: list[Json], names: tuple[str, ...]) -> dict[str, str]:
    """The saved artifacts a run presented, by file name: what a later turn hands a job as its inputs."""
    return {str(d.get("fileName")): str(d.get("artifactId") or d.get("recordId") or "")
            for e in events for d in (e.get("data") or {},)
            if e.get("type") == "x.pipeshub.artifact.presented" and d.get("fileName") in names}


def over_cap_verdict(events: list[Json], run: str, big: str, small_word: str) -> tuple[bool, str]:
    """The job ``run`` handed off went on without the input over the cap: it read the other one, its first turn names
    the skipped input, and the answer names the big file as too large (attachment.too_large) and no other."""
    children = [(e.get("data") or {}).get("childRunId") for e in events
                if e.get("type") == "child.spawned" and e.get("runId") == run]
    child = str(children[-1]) if children else ""
    skipped = [(e.get("data") or {}).get("skippedInputs") or [] for e in events
               if e.get("type") == "turn.started" and e.get("runId") == child]
    finals = [e.get("data") or {} for e in events if e.get("type") == "x.pipeshub.answer.finalized" and e.get("runId") == run]
    answer = str(finals[-1].get("answer") or "") if finals else ""
    items = [(i.get("kind"), i.get("file")) for f in finals for i in ((f.get("limits") or {}).get("items") or [])]
    read = small_word in "".join(str((e.get("data") or {}).get("text") or "") for e in events
                                 if e.get("type") == "x.pipeshub.engine.progress" and e.get("runId") == child)
    first = skipped[0] if skipped else []
    ok = (bool(child) and read and len(first) == 1 and big in json.dumps(first) and big in answer
          and items == [("attachment.too_large", big)])
    return ok, (f"MR-16: job {child or 'none'} skipped {json.dumps(first)[:160]} (want only {big}), read the other "
                f"input {read}; could-not-do items {items} (want the big file as attachment.too_large); the answer "
                f"names it {big in answer}")


def over_cap_turn(ctx: Ctx) -> tuple[bool, str]:
    """MR-16: a job given two of the conversation's own files, one over the (lowered) per-input cap, goes on with the
    other; only the big one is skipped, and the answer names it as too large."""
    m = ctx.marker
    big, small = f"plan-{m}.txt", f"small-{m}.txt"
    made = run_turn(ctx, f"shell mkdir -p out && head -c {OVER_CAP_BYTES} /dev/zero | tr '\\0' x > out/{big} && "
                         f"echo code-{m} > out/{small}\nsay made-{m}")
    arts = presented_artifacts(made.events(ctx.stack), (big, small))
    if set(arts) != {big, small}:
        return False, f"MR-16: the job saved {sorted(arts)} of {[big, small]} (conversation {made.session})"
    ctx.stack.set_process_env(INPUT_CAP_ENV)
    try:
        body = {"query": f"shell ls -la /pipeshub/inputs/ ; cat /pipeshub/inputs/{small}\nsay listed-{m}",
                "chatMode": "quick", "recordIds": [], "filters": {}, "modelKey": ctx.stack.seed["llmKey"],
                "modelName": "e2e-llm", "inputArtifacts": [{"artifactId": arts[big]}, {"artifactId": arts[small]}],
                "idempotencyKey": f"mr16-{m}"}
        before = set(root_runs(ctx, made.session))
        paced(lambda: ctx.api.ok("POST", f"{made.base}/{made.session}/runs", body))
        sh.wait_until(lambda: bool(set(root_runs(ctx, made.session)) - before), timeout=60, what="the run to be admitted")
        run = sorted(set(root_runs(ctx, made.session)) - before)[0]
        status = ended(ctx, made.session, run, timeout=600)
        ok, said = over_cap_verdict(list(ctx.stack.journal(made.session)["events"]), run, big, f"code-{m}")
        return ok and status == "completed", f"{said}; run {run} {status}"
    finally:
        ctx.stack.set_process_env({})


# R35SLACK-FU-1: the fields of an uploaded file a chat message carries (the upload's own answer has more, which the
# steering route refuses).
ATTACHMENT_FIELDS = ("recordId", "recordName", "mimeType", "extension", "virtualRecordId")
UNREAD_SLOT = "pipeshub.unread_attachments.input."


def others_upload(ctx: Ctx, name: str, content: bytes) -> Json:
    """A file another person of the org uploaded to their own chat: the admin's for a member's journey, the member's
    for the admin's (the owner shapes). The journey's person may not read it."""
    import urllib.error
    import urllib.request

    from lane import multipart

    other = ctx.stack.api if ctx.api is not ctx.stack.api else PEOPLE["member"]
    body, content_type = multipart("files", name, content, "text/plain")
    request = urllib.request.Request(f"{other.base}/api/v1/conversations/attachments/upload", data=body, method="POST")
    request.add_header("Content-Type", content_type)
    request.add_header("Authorization", f"Bearer {other.token()}")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            ref = dict(json.loads(response.read())["attachments"][0])
    except urllib.error.HTTPError as error:
        raise StackError(f"upload -> {error.code}: {error.read()[:300]!r}") from None
    return {k: ref[k] for k in ATTACHMENT_FIELDS if k in ref}


def steer_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    if ctx.path == "surface.slack":
        return slack_parked_turn(ctx, features)
    if ctx.path == "surface.mcp":
        return mcp_steer_turn(ctx, features)
    return held_steer_turn(ctx, features)


# What a /mcp call that joins a running task answers at once (chat-turns.ts QUEUED_REPLY): the answer follows on the
# first call, which the running task's stream answers.
QUEUED_REPLY = "Added to the running task."


def mcp_steer_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R36COVER-FU-2: while a run an MCP client started waits on the model, a second ``pipeshub_chat`` call names its
    conversation: the message joins that run (no second run) and that call answers at once that it was added
    (``Inprogress``, R50JFIX-6); the run answers the message, and the first call, which waits on the run, gets it."""
    say = f"steered-{ctx.marker}"
    box: dict[str, Any] = {}
    second: Any = None
    session = ""
    with HOLD_LOCK:
        ctx.stack.model.request("POST", "/__control/reset", {})
        first = threading.Thread(target=lambda: box.update(first=start_mcp(ctx, script(ctx, ["hold"], f"first-{ctx.marker}"))),
                                 daemon=True)
        first.start()
        try:
            held = holding(ctx)
            session = conversation_of(ctx, f"first-{ctx.marker}")

            def join() -> None:
                box["second"] = paced(lambda: mcp_call(ctx.api, "tools/call", {"name": "pipeshub_chat", "arguments": {
                    "query": f"Also say this when you are done.\nsay {say}", "agentId": ctx.agent,
                    "conversationId": session, "modelKey": ctx.stack.seed["llmKey"], "modelName": "e2e-llm"}}))

            if held and session:
                second = threading.Thread(target=join, daemon=True)
                second.start()
                # The second call answers once the run does, which waits on the hold: give it time to reach Node.
                second.join(timeout=20)
        finally:
            release(ctx)
        first.join(timeout=300)
        if second is not None:
            second.join(timeout=300)
    runs = root_runs(ctx, session)
    status = ended(ctx, session, runs[0]) if runs else "none"
    finals = [e.get("data") or {} for e in (list(ctx.stack.journal(session)["events"]) if session else [])
              if e.get("type") == "x.pipeshub.answer.finalized" and runs and e.get("runId") == runs[0]]
    answer = str(finals[-1].get("answer") or "") if finals else ""
    told = mcp_result(box.get("second"))
    queued = told.get("status") == "Inprogress" and told.get("answer") == QUEUED_REPLY
    first_told = say in str((box.get("first") or ("", "", ""))[2])
    ok = held and len(runs) == 1 and status == "completed" and say in answer and queued and first_told
    detail = (f"conversation {session or 'none'} held {held}; root runs {len(runs)} (want 1: the second call joined "
              f"the first); run {status}; the run answered the second message {say in answer}; the second call "
              f"answered {told.get('status')}: {str(told.get('answer'))[:80]!r} (want Inprogress: {QUEUED_REPLY!r}); "
              f"the first call got the answer {first_told}")
    return [(f, ok, detail) for f in features]


def held_steer_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R35SLACK-FU-1 (R34FIX-FU-1): while the run waits on the model the person sends another message with a file
    they may not read. The message joins the run (steering), the run answers it, and the answer names the file it was
    not given, once; the run journaled the file with the consumption (its ``unread_attachments`` slot)."""
    name = f"theirs-{ctx.marker}.txt"
    ref = others_upload(ctx, name, f"Someone else's notes {ctx.marker}.\n".encode())
    say = f"steered-{ctx.marker}"
    with HOLD_LOCK:
        t = start_held(ctx, f"first-{ctx.marker}")
        try:
            # On a job path the hold is the job's first model call, once its sandbox is up (R36COVER-FU-2).
            held = holding(ctx, timeout=240 if ctx.engine else 120)
            body = body_of(ctx, f"Also read the attached file.\nsay {say}", attachments=[ref], steer=True,
                           idempotencyKey=f"journey-{secrets.token_hex(6)}")
            body.pop("harness", None)
            steer = paced(lambda: ctx.api.request("POST", f"{t.base}/{t.session}/runs", body)) if t.session else None
        finally:
            release(ctx)
    queued = steer is not None and steer.status < 300 and bool(((steer.body or {}).get("admission") or {}).get("queued"))
    done = follow(ctx, t.session, t.run, [], [])
    events = done.events(ctx.stack)
    finals = [e.get("data") or {} for e in events if e.get("type") == "x.pipeshub.answer.finalized" and e.get("runId") == t.run]
    answer = str(finals[-1].get("answer") or "") if finals else ""
    limits = [i for f in finals for i in ((f.get("limits") or {}).get("items") or [])]
    named = [i for i in limits if i.get("file") == name and i.get("kind") == "attachment.not_accessible"]
    slot = any(UNREAD_SLOT in json.dumps((e.get("data") or {}).get("slots") or {}) for e in events
               if e.get("type") == "loop.state_saved" and e.get("runId") == t.run)
    answered = say in answer
    ok = held and queued and answered and len(named) == 1 and answer.count(name) == 1 and slot
    detail = (f"conversation {t.session} run {t.run} held {held}; the message joined the run "
              f"{steer.status if steer else 'not sent'} (queued {queued}); the run answered it {answered}; the answer "
              f"names the file it could not read {len(named)} time(s) ({[i.get('kind') for i in limits]}), in its words "
              f"{answer.count(name)} time(s); journaled with the consumption {slot}")
    return [(f, ok, detail) for f in features]


# D-SLACK-ASK 5: what the bot says to a reply in a thread whose run is parked at a card.
PARKED_WORDS = ("still waiting for an approval", "added your message")


def slack_parked_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R35SLACK-FU-3: the run in a Slack thread stops at a card (the bot hands off); the person replies in the thread.
    The bot says the run still waits, links the conversation and that the reply is added; once the person allows the
    write in PipesHub the run goes on, reads the reply and the thread gets the answer, which takes it into account."""
    args = {"title": f"journey parked {ctx.marker}", "body": "journey"}
    first = f"first-{ctx.marker}"
    session, run, how = start_slack(ctx, script(ctx, ctx.call(ctx.ask_tool(), args), first))
    handed = bool(ctx.notes.get("slackHandOff"))
    say = f"steered-{ctx.marker}"
    before = len(slack_replies(ctx))
    sent = slack_reply(ctx, f"Also say this when you are done.{SLACK_LINE_BREAK}say {say}")
    told: list[str] = []

    def parked() -> bool:
        told[:] = slack_replies(ctx)[before:]
        return any(all(w in r for w in PARKED_WORDS) and HAND_OFF_LINK in r for r in told)

    try:
        sh.wait_until(parked, timeout=SLACK_REPLY_S, what="the bot's reply to the parked thread", every=2)
        waiting = True
    except StackError:
        waiting = False
    t = follow(ctx, session, run, [], ["approved"])
    ctx.notes["say"] = say
    answered, said = slack_answered(ctx)
    effects = effects_of(ctx, args)
    ok = handed and waiting and answered and effects == 1 and sent.get("status") == 200
    detail = (f"{how}; conversation {session} run {run} ended {t.last}; handed off {handed}; the reply in the parked "
              f"thread was told the run still waits, with the link {waiting} ({[r[:200] for r in told][-1:]}); the "
              f"write ran once after it was allowed {effects == 1}; {said}")
    return [(f, ok, detail) for f in features]


JIRA_TYPE, JIRA_NAME, JIRA_ISSUE = "jiradatacenter", "Journeys Jira", "SEC-7"
JIRA_SUMMARY = "Quarterly access review"
TOOLSETS = "/api/v1/toolsets/instances"
AGENT_TOOLSETS = "/api/v1/toolsets/agents"


@contextmanager
def jira_toolset(stack: Any) -> Iterator[None]:
    """A Jira Data Center toolset on the scripted SaaS's Jira, signed in as the admin (so the default chat holds it)
    and granted to each seeded agent, for the run; the agents' toolsets put back and the instance removed after."""
    saas = stack.env.get("E2E_FAKE_SAAS_URL") or "http://pxh-fake-saas:8090"
    existing = next((i for i in (stack.api.ok("GET", TOOLSETS) or {}).get("instances") or []
                     if i.get("instanceName") == JIRA_NAME), None)
    if existing is None:
        made = stack.api.ok("POST", TOOLSETS, {"instanceName": JIRA_NAME, "toolsetType": JIRA_TYPE,
                                               "authType": "API_TOKEN", "authConfig": {"baseUrl": saas}, "baseUrl": saas})
        existing = (made or {}).get("instance") or made or {}
    instance = str(existing.get("_id") or existing.get("instanceId") or existing.get("id") or "")
    # Each person signs in to the toolset for themselves, as a person does on its card.
    for person in (stack.api, *PEOPLE.values()):
        person.ok("POST", f"{TOOLSETS}/{instance}/authenticate", {"auth": {"apiToken": stack.env["E2E_JIRA_TOKEN"]}})
    grant = {"name": JIRA_TYPE, "displayName": "Jira Data Center", "type": "app", "instanceId": instance,
             "instanceName": JIRA_NAME, "tools": [{"name": "get_issue", "fullName": f"{JIRA_TYPE}.get_issue"}]}
    before = {k: ((stack.api.ok("GET", f"/api/v1/agents/{stack.seed[k]}") or {}).get("agent") or {}).get("toolsets") or []
              for k in sh.AGENTS}
    for k, kept in before.items():
        stack.api.ok("PUT", f"/api/v1/agents/{stack.seed[k]}", {"toolsets": [*kept, grant]})
    try:
        yield
    finally:
        for k, kept in before.items():
            stack.api.request("PUT", f"/api/v1/agents/{stack.seed[k]}", {"toolsets": kept})
        stack.api.request("DELETE", f"{TOOLSETS}/{instance}")


def toolset_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    before = sum(1 for r in sh.saas(ctx.stack, "reads") if r.get("tool") == "jira.get_issue")
    call = f"tool get_issue {json.dumps({'issue_key': JIRA_ISSUE})}"
    lines = [call] if ctx.engine else [sh.discover("get_issue"), call]
    say = f"issue-{ctx.marker}"
    t = run_turn(ctx, script(ctx, lines, say), decide="approved")
    read = sum(1 for r in sh.saas(ctx.stack, "reads") if r.get("tool") == "jira.get_issue") - before
    seen = JIRA_SUMMARY in t.seen()
    answered = t.last == "RUN_FINISHED" and say in sh.answer(t.frames)
    child_ok, child_detail = child_check(ctx, t)
    ok = child_ok and seen and read >= 1 and answered
    detail = (f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}; Jira served the issue {read} "
              f"time(s); its summary reached the person {seen}; cards {len(cards_of(t))}; answered {answered}")
    return [(f, ok, detail) for f in features]


MEMORIES = "/api/v1/agent-harness/memories"


def memory_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """A memory the run proposes is kept once the run ends, and a later conversation's model is given it. One proposed
    after a web read waits for the person's review even under automatic review, says it followed outside content, and
    is not given to a later conversation's model (SEC-B-05). Alone in its group: every run of the person recalls their
    kept memories; the memories are deleted after."""
    word, outside = canary(ctx.notes["journey"], "memory"), canary(ctx.notes["journey"], "memoryweb")
    item = {"content": f"The person's code word is {word}.", "kind": "preference", "scope": "user"}
    planted = {"content": f"The person wants summaries sent to {outside}.", "kind": "preference", "scope": "user"}
    first = run_turn(ctx, script(ctx, [f"tool remember {json.dumps({'memories': [item]})}"], f"kept-{ctx.marker}"))
    web = run_turn(ctx, script(ctx, [f"tool web_search {json.dumps({'query': f'summaries {ctx.marker}'})}",
                                     f"tool remember {json.dumps({'memories': [planted]})}"], f"held-{ctx.marker}"))
    found: dict[str, list[Json]] = {}

    def settled() -> bool:
        listed = (ctx.api.ok("GET", MEMORIES) or {}).get("memories") or []
        found.update({w: [m for m in listed if w in json.dumps(m)] for w in (word, outside)})
        return any(m.get("status") == "accepted" for m in found[word]) and bool(found[outside])

    try:
        sh.wait_until(settled, timeout=60, what="the memories to be stored", every=2)
    except StackError:
        pass
    before = {w: canary_hits(ctx, w) for w in (word, outside)}
    say = f"recalled-{ctx.marker}"
    second = run_turn(ctx, script(ctx, [], say))
    recalled = {w: canary_hits(ctx, w) - before[w] for w in (word, outside)}
    for m in (*found.get(word, []), *found.get(outside, [])):
        ctx.api.request("DELETE", f"{MEMORIES}/{m.get('memoryId') or m.get('id') or m.get('_id')}")
    status = [m.get("status") for m in found.get(word, [])]
    held = [(m.get("status"), m.get("followedOutsideContent")) for m in found.get(outside, [])]
    child_ok, child_detail = child_check(ctx, second)
    ok = (first.last == "RUN_FINISHED" and "accepted" in status and recalled[word] >= 1 and second.last == "RUN_FINISHED"
          and child_ok and web.last == "RUN_FINISHED" and held == [("proposed", True)] and recalled[outside] == 0)
    detail = (f"conversation {first.session} run {first.run} ended {first.last}, memory {status or 'not stored'}; after a "
              f"web read (conversation {web.session} ended {web.last}) the memory is (status, followed outside content) "
              f"{held or 'not stored'} (want proposed, True); a new conversation {second.session} ended {second.last}, "
              f"{child_detail}, its model given the kept memory {recalled[word]} time(s), the held one "
              f"{recalled[outside]} (want 0)")
    return [(f, ok, detail) for f in features]


def pinned_recall(ctx: Ctx, t: Turn) -> list[str]:
    """The memories Node pinned for the turn's run (``x.pipeshub.memory.recalled``)."""
    memory = ((t.spec(ctx.stack).get("extensions") or {}).get("x.pipeshub") or {}).get("memory") or {}
    return [str(m) for m in memory.get("recalled") or []]


def memory_session_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R49-PLAN (ADR-MEM-01 D7): in the person's own chat, a memory kept for this conversation alone is kept when the
    run ends, pinned for the next turn of the same conversation, and never for a new one. Alone in its group (the
    memory policy is on for it); the memory is deleted after."""
    word = canary(ctx.notes["journey"], "memorysession")
    item = {"content": f"In this chat, sign answers with {word}.", "kind": "preference", "scope": "session"}
    first = run_turn(ctx, script(ctx, [f"tool remember {json.dumps({'memories': [item]})}"], f"kept-{ctx.marker}"))
    found: list[Json] = []

    def settled() -> bool:
        listed = (ctx.api.ok("GET", MEMORIES) or {}).get("memories") or []
        found[:] = [m for m in listed if word in json.dumps(m)]
        return any(m.get("status") == "accepted" for m in found)

    try:
        sh.wait_until(settled, timeout=60, what="the conversation's memory to be kept", every=2)
    except StackError:
        pass
    ids = [str(m.get("memoryId") or m.get("id") or "") for m in found]
    same = converse(ctx, f"{first.base}/{first.session}/messages/stream",
                    body_of(ctx, script(ctx, [], f"same-{ctx.marker}")), [], session=first.session)
    other = run_turn(ctx, script(ctx, [], f"other-{ctx.marker}"))
    in_same, in_other = pinned_recall(ctx, same), pinned_recall(ctx, other)
    for memory_id in ids:
        ctx.api.request("DELETE", f"{MEMORIES}/{memory_id}")
    kept = [(m.get("status"), m.get("scope")) for m in found]
    ok = (first.last == "RUN_FINISHED" and kept == [("accepted", "session")] and same.last == "RUN_FINISHED"
          and bool(ids) and ids[0] in in_same and other.last == "RUN_FINISHED" and not set(ids) & set(in_other))
    detail = (f"conversation {first.session} run {first.run} ended {first.last}, memory (status, scope) "
              f"{kept or 'not stored'} (want accepted, session); the same conversation's next run {same.run} ended "
              f"{same.last} pinned it {bool(ids) and ids[0] in in_same}; a new conversation {other.session} ended "
              f"{other.last} pinned it {bool(set(ids) & set(in_other))} (want False)")
    return [(f, ok, detail) for f in features]


PLAN_STEPS = ["Find last quarter's invoices", "Match them to the budget", "Draft the summary"]


def plan_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R49-PLAN (ADR-OWD-07 D-PLAN-1): a multi-step turn's plan is journaled as ``task.*``, streamed as the ``plan``
    frame the chat's checklist draws, and its tool call is never a step of the person's stream."""
    say = f"plan-{ctx.marker}"
    todos = [{"content": title, "status": "in_progress" if i == 0 else "pending"} for i, title in enumerate(PLAN_STEPS)]
    moved = [{**todos[0], "status": "completed"}, {**todos[1], "status": "in_progress"}, todos[2]]
    t = run_turn(ctx, script(ctx, [f"tool write_todos {json.dumps({'todos': todos})}",
                                   f"tool write_todos {json.dumps({'todos': moved})}"], say))
    tasks = [(e.get("type"), (e.get("data") or {}).get("title"), (e.get("data") or {}).get("status"))
             for e in t.events(ctx.stack) if str(e.get("type", "")).startswith("task.") and e.get("runId") == t.run]
    created = [title for kind, title, _ in tasks if kind == "task.created"]
    plans = [f.data.get("value") or {} for f in t.frames if f.type == "CUSTOM" and str(f.name) == "plan"]
    last = plans[-1] if plans else {}
    shown = [(s.get("title"), s.get("status")) for s in last.get("steps") or []]
    named = [f for f in t.frames if f.type == "TOOL_CALL_START"
             and "write_todos" in str(f.data.get("toolCallName") or f.data.get("name") or "")]
    answered = t.last == "RUN_FINISHED" and say in sh.answer(t.frames)
    ok = (answered and created == PLAN_STEPS and shown == [(PLAN_STEPS[0], "completed"),
                                                           (PLAN_STEPS[1], "in_progress"), (PLAN_STEPS[2], "pending")]
          and not named)
    detail = (f"conversation {t.session} run {t.run} ended {t.last}, answered {answered}; task.created {created}; "
              f"the last plan frame {shown or 'none'}; write_todos shown as a step {len(named)} time(s) (want 0)")
    return [(f, ok, detail) for f in features]


# --- R38-AUTHZ (ADR-GRANT-01 D9): a conversation decision is one person's --------------------------------------------

DECLINE_REASON = "Not this ticket: the team tracks it elsewhere"
NOT_OFFERED = "CONVERSATION_GRANT_NOT_OFFERED"
APPROVER_REQUIRED = "APPROVER_REQUIRED"


def resolutions(events: list[Json], run: str) -> list[Json]:
    return [e.get("data") or {} for e in events if e.get("type") == "interaction.resolved" and e.get("runId") == run]


def answer_text(resolved: Json) -> str:
    """What the resolution says to the run (a denial's reason), from its inline answer."""
    import base64

    ref = str((resolved.get("answerRef") or {}).get("ref") or "")
    return base64.b64decode(ref[len("inline:"):]).decode(errors="replace") if ref.startswith("inline:") else ""


def frame_seq(frame: Any) -> int:
    """The journal position a frame was projected at (its id ``j:<seq>.<n>/…``)."""
    try:
        return int(str(frame.id).split(":", 1)[1].split(".", 1)[0])
    except (IndexError, ValueError):
        return -1


def node_answered(ctx: Ctx) -> Callable[[str, Json], bool]:
    """Whether Node already answered the card's ask (a held decline's own answer): the person has nothing to do."""
    def answered(session: str, value: Json) -> bool:
        events = list(ctx.stack.journal(session)["events"]) if session else []
        return any(e.get("type") == "interaction.resolved"
                   and (e.get("data") or {}).get("interactionId") == value.get("interactionId")
                   and e.get("runId") == (value.get("childRunId") or e.get("runId")) for e in events)
    return answered


def carry_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """R36LIVE-FU-1: the person declines a call with a reason, and the same call made
    again later in the conversation by a run of theirs is declined by Node with that reason (``channel: system``), so
    nobody is asked twice: its card is drawn with its answer, already decided, never as an open ask. On a job path
    the default chat hands off, the job's call is the one declined and the parent makes it again once the job
    returns; on the loop the next turn makes it again. Both send the same arguments: the call names no destination,
    so the decline is keyed by them (argsDigest)."""
    args = {"title": f"journey carry {ctx.marker}", "body": "journey"}
    tool = ctx.ask_tool()
    deny: Json = {"decision": "denied", "reason": f"{DECLINE_REASON} ({ctx.marker})"}
    say = f"carried-{ctx.marker}"
    if ctx.engine:
        again = [f"handoff-then {sh.discover(tool).removeprefix('tool ')}", f"handoff-then {tool} {json.dumps(args)}"]
        t = run_turn(ctx, script(ctx, [f"tool {tool} {json.dumps(args)}", *again], say), decide=deny,
                     decided=node_answered(ctx))
        first, last, repeated_by = t, t, t.run
        frames = t.frames
    else:
        first = run_turn(ctx, script(ctx, ctx.call(tool, args), f"first-{ctx.marker}"), decide=deny)
        last = converse(ctx, f"{first.base}/{first.session}/messages/stream",
                        body_of(ctx, script(ctx, ctx.call(tool, args), say)), [deny], session=first.session,
                        decided=node_answered(ctx))
        # Deep mode makes the call in a sub-agent the turn hands off to: the repeat is that child's, not the turn's.
        repeated_by = last.child or last.run
        frames = [*first.frames, *last.frames]
    events = list(ctx.stack.journal(first.session)["events"]) if first.session else []
    declined = [e.get("data") or {} for e in events if e.get("type") == "interaction.declined" and not
                (e.get("data") or {}).get("fromRunId")]
    by_system = [r for r in resolutions(events, repeated_by) if r.get("channel") == "system"]
    # Loop interaction ids repeat across runs: an ask is its run and its id.
    system_ids = {(repeated_by, r.get("interactionId")) for r in by_system}
    answered_at = {(e.get("runId"), (e.get("data") or {}).get("interactionId")): e.get("seq") for e in events
                   if e.get("type") == "interaction.resolved"}
    # The person answers only the first card. A repeat Node declined is drawn with its answer (held, D7/D9), a receipt;
    # only a later turn's may show a moment before it (ADR-GRANT-01 D9: its run never held the decline's copy).
    cards = [((f.data.get("value") or {}).get("childRunId") or f.data.get("runId"),
              (f.data.get("value") or {}).get("interactionId"), frame_seq(f)) for f in frames if card(f)]
    asked = [c for c in cards if c[:2] not in system_ids]
    flashed = [c for c in cards if c[:2] in system_ids and c[2] < (answered_at.get(c[:2]) or 0)]
    told = [answer_text(r) for r in by_system]
    # The parent's stream folds a sub-agent into its start and end: the repeat's tool result is on the child's own.
    told_in = (last.frames if repeated_by == last.run else list(ctx.api.stream(
        "GET", f"{last.base}/{last.session}/runs/{repeated_by}/stream").frames))
    said = any(deny["reason"] in json.dumps(f.data) for f in told_in if f.type == "TOOL_CALL_RESULT"
               and f.data.get("runId") in (repeated_by, None))
    effects = effects_of(ctx, args)
    answered = last.last == "RUN_FINISHED" and say in sh.answer(last.frames)
    child_ok, child_detail = child_check(ctx, first)
    ok = (child_ok and len(asked) == 1 and not (ctx.engine and flashed) and bool(declined) and declined[0].get("reason") == deny["reason"]
          and [r.get("decision") for r in by_system] == ["denied"] and told == [deny["reason"]] and said
          and effects == 0 and answered)
    detail = (f"conversation {first.session} run {first.run}" + (f", child {first.child}" if first.child else "")
              + f", repeated by run {repeated_by} ended {last.last}; {child_detail}; cards drawn {len(cards)}, the "
              f"person asked {len(asked)} (want 1), the repeat's drawn before its answer {len(flashed)} (want 0 in "
              f"the same run); the decline written "
              f"{[(d.get('reason'), bool(d.get('key'))) for d in declined]}; the repeated call resolved "
              f"{[(r.get('decision'), r.get('channel')) for r in by_system]} (want denied by system) with the reason "
              f"{told}; its tool result tells the model the reason {said}; written {effects} (want 0); answered {answered}")
    return [(f, ok, detail) for f in features]


def shared_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """SEC-B-01, SEC-C-04: the person (the admin, Alice) shares her default-chat conversation with a colleague (the
    org's member, Bob) for writing (OWD-19). Bob's turn reads the shared knowledge, then searches the web: the flow
    guard asks him, and he allows it for the conversation. Alice's turn then makes the same search (same tool and
    destination) after the same read: she is asked herself (Bob's grant is his, ADR-GRANT-01 D9), and Bob's "for the
    conversation" answer on her card is refused (he may not answer her ask at all, AP-1, or, as an approver, not for
    the conversation, CONVERSATION_GRANT_NOT_OFFERED)."""
    alice, bob = ctx.api, PEOPLE["member"]
    m = ctx.marker
    opened = run_turn(ctx, script(ctx, [], f"open-{m}"))
    path = f"{opened.base}/{opened.session}"
    # The web app's own routes, end to end (R38AUTHZ-FU-1, FU-2): the share keeps its level, and the collaborator's
    # turn goes through the chat's message route as the owner's does.
    alice.ok("POST", f"{path}/share", {"userIds": [PEOPLE_IDS["member"]], "accessLevel": "write"})
    shared = {str(s.get("userId")): s.get("accessLevel")
              for s in ((alice.ok("GET", path) or {}).get("conversation") or {}).get("sharedWith") or []}
    via_api = shared.get(PEOPLE_IDS["member"])
    search = f"tool web_search {json.dumps({'query': f'expenses policy {m}'})}"
    lines = [kb_search(f"shared-{m}"), search]
    bobs = converse(ctx, f"{path}/messages/stream", body_of(ctx, script(ctx, lines, f"bob-{m}")),
                    [{"decision": "approved", "scope": "conversation"}], session=opened.session, api=bob)
    bob_run = bobs.run
    bob_cards = [f for f in bobs.frames if card(f)]
    hers = converse(ctx, f"{path}/messages/stream", body_of(ctx, script(ctx, lines, f"alice-{m}")), [],
                    session=opened.session)
    her_cards = [f.data.get("value") or {} for f in hers.frames if card(f)]
    refused: Any = None
    if her_cards:
        ask = her_cards[0]
        answer = bob.request("POST", f"{path}/runs/{ask.get('childRunId') or hers.run}/interactions/"
                                     f"{ask.get('interactionId')}/decision",
                             {"decision": "approved", "scope": "conversation",
                              "idempotencyKey": f"journey-{secrets.token_hex(6)}"})
        error = (answer.body or {}).get("error") if isinstance(answer.body, dict) else None
        refused = (answer.status, (error or {}).get("code") if isinstance(error, dict) else None)
        hers = follow(ctx, opened.session, hers.run, hers.frames, ["approved"])
    events = list(ctx.stack.journal(opened.session)["events"])

    def of(run: str, kind: str) -> list[Json]:
        return [e.get("data") or {} for e in events if e.get("runId") == run and e.get("type") == kind]

    member = f"p_{PEOPLE_IDS['member']}"
    offered = [x.get("conversationGrant") for x in of(bob_run, "interaction.explained")]
    granted = [(g.get("by") == member, g.get("scope"), g.get("key")) for g in of(bob_run, "interaction.granted")]
    asked = of(hers.run, "interaction.explained")
    same_call = bool(granted) and any(a.get("destination", {}).get("key") == granted[0][2] for a in asked)
    hers_resolved = [(r.get("decision"), r.get("channel"), r.get("by") == member) for r in resolutions(events, hers.run)]
    # Bob may not answer Alice's ask at all (AP-1: her run, her or an approver); an approver who may is refused the
    # conversation scope (D9.2). Either way nothing Bob does answers it for her conversation.
    not_his = refused is not None and ((refused[0] == 409 and NOT_OFFERED in str(refused[1]))
                                       or (refused[0] == 403 and APPROVER_REQUIRED in str(refused[1])))
    answered = hers.last == "RUN_FINISHED" and f"alice-{m}" in sh.answer(hers.frames)
    ok = (via_api == "write" and bobs.last == "RUN_FINISHED" and len(bob_cards) == 1 and offered == [True]
          and [g[:2] for g in granted] == [(True, "conversation")] and same_call and len(her_cards) == 1 and not_his
          and hers_resolved == [("approved", "web", False)] and answered)
    detail = (f"conversation {opened.session} shared with the member through the share route, stored "
              f"{via_api!r} (want 'write'); the member's turn through the chat's message route: run "
              f"{bob_run} ended {bobs.last}, cards {len(bob_cards)}, offered for the conversation {offered}, his grant "
              f"(his, scope) {[g[:2] for g in granted]}; the owner's run {hers.run} ended {hers.last}: the same call "
              f"(tool and destination) {same_call}, cards {len(her_cards)} (want 1: the member's grant is his); the "
              f"member's for-the-conversation answer on her card refused {refused}; her ask resolved (decision, "
              f"channel, by the member) {hers_resolved} (want approved on the web by her); answered {answered}")
    return [(f, ok, detail) for f in features]


# R31JOURNEY-FU-5: erasure as a journey. A conversation (or a person) holding a unique word in its message, its answer
# and a file it saved is deleted; once its erasure job completes the word is nowhere: not in the conversation's routes,
# the product's search, any MongoDB document, Neo4j node or Qdrant point (erase_world.py's store readers), and the
# journal reader refuses the conversation. Each store is first shown to hold the word, so no check passes blind.
ERASURE_WAIT_S = 240


def _world(ctx: Ctx) -> Any:
    """erase_world.py's store readers over this stack (its World, without the durable lane's attach)."""
    import erase_world

    world = erase_world.World.__new__(erase_world.World)
    world.stack, world.api, world.org = ctx.stack, ctx.stack.api, ctx.stack.org_id
    world.project = (ctx.stack.runtime.run_dir / "project").read_text().strip()
    return world


def _erased(ctx: Ctx, sessions: list[str]) -> bool:
    try:
        sh.wait_until(lambda: int(ctx.stack.mongo(
            f"print(d.agentSessionErasures.countDocuments({{orgId: {json.dumps(ctx.stack.org_id)},"
            f" sessionId: {{$in: {json.dumps(sessions)}}}, status: 'completed'}}))").strip() or 0) >= len(sessions),
            timeout=ERASURE_WAIT_S, what="the conversations' erasure jobs", every=3)
        return True
    except StackError:
        return False


def _nowhere(ctx: Ctx, word: str) -> tuple[bool, str]:
    """No store holds the word (polled: an index or a projection may lag the erasure job)."""
    world = _world(ctx)
    seen: dict[str, Any] = {}

    def clean() -> bool:
        seen.update(mongo=world.mongo_holders([word]), qdrant=world.qdrant_holders([word]),
                    neo4j=world.neo4j_holders([word]), search=word in world.search(word))
        return not seen["mongo"] and not seen["qdrant"] and not seen["neo4j"] and not seen["search"]

    try:
        sh.wait_until(clean, timeout=180, what="no store to hold the erased word", every=5)
        return True, f"no store holds it (MongoDB {seen['mongo']}, Qdrant {seen['qdrant']}, Neo4j {seen['neo4j']})"
    except StackError:
        return False, (f"still held: MongoDB {seen.get('mongo')}, Qdrant points {seen.get('qdrant')}, Neo4j nodes "
                       f"{seen.get('neo4j')}, the product's search finds it {seen.get('search')}")


def erase_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    if ctx.shape_id == "service":
        return person_erase_turn(ctx, features)
    word = f"pxherase{secrets.token_hex(5)}"
    name = f"erase-{ctx.marker}.txt"
    made = f"write {name} {word}" if ctx.engine else save_line(name, f"notes {word}")
    t = run_turn(ctx, f"Keep the code {word} for me.\n" + script(ctx, [made], f"kept {word}"))
    return _erase_and_check(ctx, features, word, [t.session], f"conversation {t.session} run {t.run} ended {t.last}",
                            lambda: ctx.api.request("DELETE", f"{base_of(ctx)}/{t.session}"))


def _erase_and_check(ctx: Ctx, features: list[str], word: str, sessions: list[str], head: str,
                     delete: Callable[[], Any]) -> list[Line]:
    world = _world(ctx)
    planted = bool(world.mongo_holders([word]))
    gone = delete()
    erased = _erased(ctx, sessions)
    reads = [ctx.api.request("GET", f"{base_of(ctx)}/{s}").status for s in sessions]
    journal = all(world.journal_text(s) == "" or word not in world.journal_text(s) for s in sessions)
    clean, where = _nowhere(ctx, word)
    ok = (planted and gone is not None and gone.status < 300 and erased and all(r in (403, 404, 410) for r in reads)
          and journal and clean)
    detail = (f"{head}; the word was stored before {planted}; delete {gone.status if gone else 'not sent'}; erasure "
              f"jobs completed {erased}; reads after {reads}; the journal holds it {not journal}; {where}")
    return [(f, ok, detail) for f in features]


def person_erase_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    """A person of the org (made for the journey) chats with the service agent, then an admin deletes them: their
    conversations are erased with them (principal deletion, ``user_delete``) and the word is nowhere after."""
    email = f"erase-{ctx.marker}@acme-demo.example"
    password = f"Erase-{secrets.token_hex(6)}!1a"
    made = ctx.stack.api.ok("POST", "/api/v1/users", {"fullName": "Erin Erase", "email": email, "role": "member",
                                                      "password": password})
    user = (made or {}).get("user") or made or {}
    user_id = str(user.get("_id") or user.get("id") or "")
    person = sh.Api(ctx.stack.api.base, email, password)
    word = f"pxherase{secrets.token_hex(5)}"
    body = body_of(ctx, f"Keep the code {word} for me.\nsay kept {word}")
    stream = paced(lambda: person.stream("POST", f"{base_of(ctx)}/stream", body, stop=stops))
    session, run = ids_of(list(stream.frames))
    last = stream.frames[-1].type if stream.frames else "none"

    def delete() -> Any:
        return ctx.stack.api.request("DELETE", f"/api/v1/users/{user_id}")

    lines = _erase_and_check(ctx, features, word, [session] if session else [],
                             f"a new member's conversation {session} run {run} ended {last}", delete)
    try:
        sh.wait_until(lambda: int(ctx.stack.mongo(
            f"print(d.agentPrincipalErasures.countDocuments({{orgId: {json.dumps(ctx.stack.org_id)}, kind: 'user',"
            f" userId: {json.dumps(user_id)}}}))").strip() or 0) == 0, timeout=ERASURE_WAIT_S,
            what="the person's erasure job", every=3)
        principal = True
    except StackError:
        principal = False
    return [(f, ok and principal, f"{d}; the person's erasure job finished {principal}") for f, ok, d in lines]


# R31JOURNEY-FU-5: the answer contract on a job (ADR-RUN-03). The person asks about a subject no knowledge holds; the
# job searches for it and finds nothing. The answer leads with the plain not-found note and its confidence is Low,
# never a picked "Medium"; where the default chat asks first (its own search found nothing), the person goes on with
# "Use what you can find", as the card offers (AC-E1).
USE_FOUND = "Use what you can find"


def finalized(ctx: Ctx, t: Turn) -> list[Json]:
    """The answers the turn's own run finalized (a job's own answer is its child run's, journaled in the same
    conversation)."""
    return [e.get("data") or {} for e in t.events(ctx.stack)
            if e.get("type") == "x.pipeshub.answer.finalized" and e.get("runId") == t.run]


def contract_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    subject = f"Zephyr Quarry {ctx.marker}"
    search = json.dumps({"query": subject})
    say = f"contract-{ctx.marker}"
    text = "\n".join([f'Look up "{subject}" for me.',
                      f"tool search_internal_knowledge {search} || knowledgegraph__search {search}", f"say {say}"])
    if ctx.path == "surface.slack":
        # The default assistant's bot hands the work off (R31JOURNEY-FU-2): the thread is told when it is done.
        ctx.notes["expect"] = subject
    t = run_turn(ctx, text)
    asked = [f for f in t.frames if f.type == "CUSTOM" and "question" in str(f.name)]
    went_on = None
    if ctx.path == "surface.slack" and any(SLACK_QUESTION in r for r in slack_replies(ctx)):
        # Asked back in the thread: the person answers there, and the run that goes on is the conversation's newest.
        slack_reply(ctx, USE_FOUND)
        ctx.notes["slackAnswer"] = slack_answered(ctx)
        runs = root_runs(ctx, t.session)
        latest = runs[-1] if runs else t.run
        went_on = follow(ctx, t.session, latest, [], [])
        asked = asked or [None]
    elif asked and t.session:
        value = asked[-1].data.get("value") or {}
        questions = value.get("questions") or (value.get("toolData") or {}).get("questions") or []
        question = str((questions[0] if questions else {}).get("question") or "")
        reply = f'User selections:\n1. "{question}" → {USE_FOUND}'
        went_on = converse(ctx, f"{t.base}/{t.session}/messages/stream", body_of(ctx, reply, answers=t.run), [],
                          session=t.session, run=t.run)
        went_on.extra.pop("events", None)
    done = went_on or t
    finals = [f for f in finalized(ctx, done) if f.get("outcome") == "answered"]
    last = finals[-1] if finals else {}
    found = last.get("notFound") or {}
    leads = bool(found.get("line")) and str(last.get("answer") or "").startswith(str(found.get("line")))
    low = last.get("confidence") == "Low"
    named = subject in str(found.get("subject") or "") or str(found.get("subject") or "") in subject
    child_ok, child_detail = child_check(ctx, done)
    ok = done.last in sh.TERMINAL and bool(found) and named and leads and low and child_ok
    detail = (f"conversation {t.session} run {t.run} ended {done.last}; {child_detail}; asked first {bool(asked)}"
              f"{' and went on with ' + USE_FOUND if went_on else ''}; the answer's not-found note "
              f"{json.dumps({k: found.get(k) for k in ('subject', 'hits', 'proceeded')})}, leads the answer {leads}; "
              f"confidence {last.get('confidence')} (want Low)")
    return [(f, ok, detail) for f in features]


# R31JOURNEY-FU-4: the MCP hub and an OAuth server, on a stack brought up with E2E_MCP_HUB=1 (docker-compose.mcp.yml:
# the hub, STDIO runners and the scripted servers of fake_saas/kinds.py). Per journey, as an admin and the person set
# them up (integration-tests/harness/e2e/mcp_stack.py's McpAdmin): an org STDIO server whose secret the admin enters
# once for everyone (useAdminAuth), its manifest approved, and an OAuth server the journey's person signs in to; the
# saved agent of the shape is granted their tools, the default chat holds them whole. Removed after; alone in its
# group, as the default chat holds every server its person signed in to and an expiry ends every owner's token.
HUB_SECRET_ENV = "ECHO_API_TOKEN"


@contextmanager
def hub_servers(ctx: Ctx, features: list[str]) -> Iterator[Json]:
    from mcp_stack import STDIO_PROGRAM, McpAdmin, Server, canary

    admin = McpAdmin(ctx.stack)
    admin.suffix = ctx.marker
    made: list[Server] = []
    granted: dict[str, Any] = {}
    out: Json = {"admin": admin}
    try:
        if "mcp.hub" in features:
            secret = canary("hub")
            answer = admin.create({"name": f"hub{ctx.marker}", "transport": "stdio", "authMode": "api_token",
                                   "command": "python3", "args": [STDIO_PROGRAM], "requiredEnv": [HUB_SECRET_ENV],
                                   "useAdminAuth": True})
            if answer.status != 201:
                raise StackError(f"creating the STDIO server -> {answer.status}: {str(answer.body)[:300]}")
            instance = (answer.body.get("instance") or answer.body) if isinstance(answer.body, dict) else {}
            stdio = Server(f"hub{ctx.marker}", str(instance.get("_id") or instance.get("instanceId")), None, [secret])
            made.append(stdio)
            answered = admin.authenticate(stdio, {"apiToken": secret})
            if answered.status >= 300:
                raise StackError(f"the STDIO server's secret -> {answered.status}: {str(answered.body)[:300]}")
            out["approved"] = admin.approve(stdio, ["env_digest"])
            out.update(stdio=stdio, secret=secret)
        if "mcp.oauth" in features:
            name = f"oa{ctx.marker}"
            oauth = admin.http_server("oa", auth_mode="oauth", fake_auth="oauth",
                                      authorizationUrl=f"http://pxh-fake-mcp:8091/oauth/{name}/authorize",
                                      tokenUrl=f"http://pxh-fake-mcp:8091/oauth/{name}/token")
            made.append(oauth)
            client = {"clientId": f"journeys-{ctx.marker}", "clientSecret": secrets.token_hex(12)}
            admin.fake.ok("POST", f"/__control/servers/{oauth.fake_id}/oauth/clients", client)
            ctx.stack.api.ok("PUT", f"/api/v1/mcp-servers/instances/{oauth.instance_id}/oauth-config", client)
            # The admin reads and approves the manifest with their own sign-in; the journey's person signs in too.
            admin.oauth_connect(oauth)
            if ctx.api is not ctx.stack.api:
                admin.oauth_connect(oauth, api=ctx.api)
            admin.approve(oauth, ["whoami"])
            out["oauth"] = oauth
        if ctx.agent is not None:
            stored = (ctx.stack.api.ok("GET", f"/api/v1/agents/{ctx.agent}") or {}).get("agent") or {}
            kept = [{"instanceId": m.get("instanceId"), "name": m.get("name"),
                     "tools": [{"name": x.get("name"), "fullName": x.get("fullName")} for x in m.get("tools") or []]}
                    for m in stored.get("mcpServers") or []]
            granted[ctx.agent] = kept
            ctx.stack.api.ok("PUT", f"/api/v1/agents/{ctx.agent}", {"mcpServers": [*kept, *(
                {"instanceId": s.instance_id, "name": s.name,
                 "tools": [{"name": raw, "fullName": full} for raw, full in sorted(s.tools.items())]} for s in made)]})
        yield out
    finally:
        for key, kept in granted.items():
            ctx.stack.api.request("PUT", f"/api/v1/agents/{key}", {"mcpServers": kept})
        for server in made:
            ctx.stack.api.request("DELETE", f"/api/v1/mcp-servers/instances/{server.instance_id}")


def hub_call(ctx: Ctx, exported: str, args: Json) -> list[str]:
    """A hub server's tool as the path's model calls it: an engine names it, the loop finds it first (D-LAZY-4)."""
    line = f"tool {exported} {json.dumps(args)}"
    return [line] if ctx.engine else [sh.discover(exported), line]


def hub_turn(ctx: Ctx, features: list[str]) -> list[Line]:
    if ctx.stack.env.get("E2E_MCP_HUB") != "1":
        return [(f, False, "this stack has no MCP hub: bring it up with E2E_MCP_HUB=1 (the hub lane)") for f in features]
    with hub_servers(ctx, features) as hub:
        return _hub_turn(ctx, features, hub)


def _hub_turn(ctx: Ctx, features: list[str], hub: Json) -> list[Line]:
    lines: list[str] = []
    if "stdio" in hub:
        lines += hub_call(ctx, hub["stdio"].tools["env_digest"], {"name": HUB_SECRET_ENV})
    if "oauth" in hub:
        lines += hub_call(ctx, hub["oauth"].tools["whoami"], {})
    say = f"hub-{ctx.marker}"
    t = run_turn(ctx, script(ctx, lines, say), decide="approved")
    seen = t.seen()
    finished = t.last in sh.TERMINAL
    child_ok, child_detail = child_check(ctx, t)
    head = f"conversation {t.session} run {t.run} ended {t.last}; {child_detail}"
    out: list[Line] = []
    if "stdio" in hub:
        # OWD-1 (WP-06): the server holds a stand-in for its secret, which the egress proxy swaps on the way out, so
        # it answers with the stand-in's digest: a value, never the secret's.
        digests = set(re.findall(r"\b[0-9a-f]{64}\b", seen))
        answered = bool(digests) and hashlib.sha256(hub["secret"].encode()).hexdigest() not in digests
        if ctx.harness and not digests:
            # Today's loop cannot hold a call for a person (X-CLASSIC): the server's tool, which declares no effect and
            # so asks, is found and its call is refused with the reason, never run unasked.
            answered = hub["stdio"].tools["env_digest"] in seen and "needs someone to approve it" in seen
        approved = bool(((hub.get("approved") or {}).get("approved") or {}).get("approvedBy"))
        runners = subprocess.run(["docker", "ps", "-q", "--filter", "label=pipeshub.role=mcp-runner", "--filter",
                                  f"label=orgId={ctx.stack.org_id}"], capture_output=True, text=True, timeout=30)
        ran = bool(runners.stdout.strip())
        leaked = hub["secret"] in seen
        out.append(("mcp.hub", finished and child_ok and answered and approved and ran and not leaked,
                    f"{head}; the manifest approved by the admin {approved}; "
                    + (f"the tool found and its call refused for approval (today's loop) {answered}"
                       if ctx.harness and not digests else
                       f"the STDIO server answered, holding a stand-in for its secret, not the secret {answered}")
                    + f" (the secret itself in the stream {leaked}); a runner sandbox ran it {ran}"))
    if "oauth" in hub:
        out.append(("mcp.oauth", *_oauth_checked(ctx, hub, t, finished and child_ok, head)))
    return [line for line in out if line[0] in features]


def _oauth_checked(ctx: Ctx, hub: Json, first: Turn, ok_so_far: bool, head: str) -> tuple[bool, str]:
    """The person's own sign-in answered the call; after the provider ends every access token, the next turn's
    call is refused once, refreshed once with the refresh token and answered (K-8)."""
    admin, oauth = hub["admin"], hub["oauth"]
    view = admin.seen(oauth.fake_id)
    live = set((view.get("oauth") or {}).get("live") or [])
    calls = view.get("calls") or []
    signed = bool(calls) and {c.get("credentialDigest") for c in calls} <= live and f"nonce={view.get('nonce')}" in first.seen()
    issued_before = len((view.get("oauth") or {}).get("issued") or [])
    refused_before = len(view.get("refused") or [])
    admin.fake.ok("POST", f"/__control/servers/{oauth.fake_id}/oauth/expire", {})
    again = run_turn(ctx, script(ctx, hub_call(ctx, oauth.tools["whoami"], {}), f"again-{ctx.marker}"), decide="approved")
    after = admin.seen(oauth.fake_id)
    grants = [i.get("grant") for i in ((after.get("oauth") or {}).get("issued") or [])[issued_before:]]
    refused = len(after.get("refused") or []) - refused_before
    last = (after.get("calls") or [{}])[-1]
    refreshed = grants == ["refresh_token"] and refused >= 1 and last.get("credentialDigest") in set(
        (after.get("oauth") or {}).get("live") or [])
    answered = f"nonce={after.get('nonce')}" in again.seen() and again.last in sh.TERMINAL
    leaked = any(re.search(r"\b(?:oat|ort|dcs)-[A-Za-z0-9_-]{16,}", s) for s in (first.seen(), again.seen()))
    ok = ok_so_far and signed and refreshed and answered and not leaked
    return ok, (f"{head}; the person's own sign-in answered the call {signed}; after the provider ended the token: "
                f"refused {refused}, new grants {grants} (want one refresh_token), the call answered again {answered}; "
                f"a token in the stream {leaked}")


# A person's /run (SLASH3): one shell line run by run_code in the conversation's sandbox, no model call. Each probe
# that runs on the command path is a command: what it proves is read from the run's stream and its pinned spec.
COMMANDS: dict[str, str] = {
    "use": "printf made-{m} > journey-{m}.txt && cat journey-{m}.txt",
    "sandbox": "python3 -c \"print('sandbox-' + 'ok')\"",
    "oom": BALLOON,
    "cancel": "sleep 120",
    "delete": "sleep 120",
    "erase": "printf {w} > erase-{m}.txt && cat erase-{m}.txt",
}


def start_command(ctx: Ctx, command: str) -> Turn:
    opened = run_turn(ctx, f"say open-{ctx.marker}")
    answer = paced(lambda: ctx.api.request("POST", f"{opened.base}/{opened.session}/commands/run", {
        "command": command, "timeoutS": 120, "idempotencyKey": f"slash-run-{secrets.token_hex(8)}",
        "chatMode": "agent", "modelKey": ctx.stack.seed["llmKey"], "modelName": "e2e-llm"}))
    if answer.status >= 300:
        raise StackError(f"commands/run -> {answer.status}: {json.dumps(answer.body)[:300]}")
    run = str((((answer.body or {}).get("admission") or {}).get("run") or {}).get("runId") or "")
    return Turn([], opened.session, run, opened.base, None)


def command_turn(ctx: Ctx, features: list[str], turn: str) -> list[Line]:
    marker = ctx.marker
    if turn == "erase":
        word = f"pxherase{secrets.token_hex(5)}"
        t = start_command(ctx, COMMANDS[turn].format(m=marker, w=word))
        t = follow(ctx, t.session, t.run, [], [])
        return _erase_and_check(ctx, features, word, [t.session], f"conversation {t.session} /run run {t.run} "
                                f"ended {t.last}", lambda: ctx.api.request("DELETE", f"{t.base}/{t.session}"))
    t = start_command(ctx, COMMANDS[turn].format(m=marker))
    head = f"conversation {t.session} /run run {t.run or 'none'}"
    if turn in ("cancel", "delete"):
        sh.wait_until(lambda: any(e.get("type") == "run.claimed" and e.get("runId") == t.run for e in
                                  ctx.stack.journal(t.session)["events"]), timeout=60, what="the command to start")
        if turn == "cancel":
            answer = ctx.api.request("POST", f"{t.base}/{t.session}/runs/{t.run}/cancel",
                                           {"idempotencyKey": f"journey-{secrets.token_hex(6)}"})
            status = ended(ctx, t.session, t.run)
            return [(f, answer.status < 300 and status == "cancelled",
                     f"{head}; cancel {answer.status}, run {status}") for f in features]
        gone = ctx.api.request("DELETE", f"{t.base}/{t.session}")
        after = ctx.api.request("GET", f"{t.base}/{t.session}")
        status = ended(ctx, t.session, t.run)
        ok = gone.status < 300 and after.status in (403, 404, 410) and status != "running"
        return [(f, ok, f"{head}; delete {gone.status}, read after {after.status}, run {status}") for f in features]
    t = follow(ctx, t.session, t.run, [], [])
    seen, status = t.seen(), ended(ctx, t.session, t.run)
    if turn == "use":
        ok, detail = f"made-{marker}" in seen and status == "completed", f"the file's content read back {f'made-{marker}' in seen}"
    elif turn == "sandbox":
        profile = (t.spec(ctx.stack).get("grants") or {}).get("sandboxProfile")
        ok = "sandbox-ok" in seen and profile == "agent-code" and status == "completed"
        detail = f"output seen {'sandbox-ok' in seen}; pinned sandboxProfile {profile}"
    else:
        named = any(word in seen for word in OOM_WORDS)
        ok, detail = named and status in ("completed", "failed"), f"the memory limit's end named to the person {named}"
    return [(f, ok, f"{head} ended {t.last} ({status}); {detail}") for f in features]


PROBES: dict[str, Callable[[Ctx, list[str]], list[Line]]] = {
    "flow": flow_turn,
    "carry": carry_turn,
    "shared": shared_turn,
    "attach": attach_turn,
    "toolset": toolset_turn,
    "memory": memory_turn,
    "memory_session": memory_session_turn,
    "plan": plan_turn,
    "web": web_turn,
    "use": use_turn,
    "deny": lambda ctx, fs: card_turn(ctx, fs, "denied"),
    "allow": lambda ctx, fs: card_turn(ctx, fs, "approved"),
    "question": question_turn,
    "cancel": cancel_turn,
    "delete": delete_turn,
    "budget": budget_turn,
    "pins": pins_turn,
    "find_read": find_read_turn,
    "sandbox": sandbox_turn,
    "repo_allow": repo_allow_turn,
    "repo_deny": repo_deny_turn,
    "repo_named": repo_named_turn,
    "repo_unread": repo_unread_turn,
    "oom": oom_turn,
    "steer": steer_turn,
    "erase": erase_turn,
    "contract": lambda ctx, fs: contract_turn(ctx, fs),
    "hub": hub_turn,
}


# --- shapes: the owner fixture on the stack, and the engine setting a journey group needs ---------------------------


def apply_owner_shape(stack: Any, shape: Json) -> None:
    """The person's servers the owner fixture names, made once (each a server of the stack's scripted SaaS)."""
    mine = stack.api.ok("GET", "/api/v1/mcp-servers/my-mcp-servers") or {}
    have = {i.get("name"): i for i in mine.get("instances") or []}
    for name in shape["owner"]["servers"]:
        # Each server's own URL, so the scripted SaaS counts its listings apart (fake_saas LISTINGS).
        url = shape["owner"]["serverUrl"] + ("" if name == sh.MCP_NAME else f"?server={name}")
        body = {"name": name, "transport": "streamable_http", "authMode": "none", "url": url}
        instance = have.get(name)
        if instance is None:
            stack.api.ok("POST", "/api/v1/mcp-servers/instances", body)
        elif instance.get("url") not in (None, url):
            stack.api.ok("PUT", f"/api/v1/mcp-servers/instances/{instance.get('_id') or instance.get('instanceId')}",
                         body)


def setting_of(path: str, shape: str) -> tuple[str | None, str]:
    """(the org's default coding engine, the person's choice) a journey needs; None: the org's is left alone."""
    engine = ENGINE_OF.get(path)
    if engine and shape == "owner":
        return engine, sh.ORG_DEFAULT
    if engine and shape == "owner_pinned":
        return None, engine
    if shape == "owner_pinned":
        # The default chat with the person's own engine pinned (R33JGAPS-FU-4); its turns stay on the parent loop.
        return None, PINNED_ENGINE
    if path in ("assistant.agent", "command", CHAT_MODE_PATH) or (
            (path.startswith("resume.") or path == "recovery") and shape == "owner"):
        return None, sh.NO_ENGINE
    return None, "*"


@contextmanager
def engine_setting(stack: Any, org: str | None, choice: str) -> Iterator[None]:
    """The org's default engine and the person's choice a group needs, put back after it. Every release engine, an
    ACP agent too, runs with no org setting; a build whose orgs opt engines in does so in ``engines.prepare_org``."""
    import engines

    previous = (stack.api.ok("GET", sh.CODING_ENGINE) or {}).get("orgDefault")
    engine = org or (choice if choice not in (sh.ORG_DEFAULT, sh.NO_ENGINE, "*") else None)
    undo = engines.prepare_org(stack.api.ok, [engine]) if engine else (lambda: None)
    try:
        if org is not None:
            stack.api.ok("PUT", sh.ORG_CODING_ENGINE, {"defaultEngine": org})
        with sh.coding_engine_choice(stack.api, choice) if choice != "*" else nullcontext():
            yield
    finally:
        if org is not None:
            stack.api.request("PUT", sh.ORG_CODING_ENGINE, {"defaultEngine": previous})
        undo()


# The service shape: a saved agent that runs as a service account (principal agent_service), made for the run as the
# seeded loop agent is (its knowledge, the org's MCP server by its named tools, its skill), removed after.
SERVICE_AGENT: dict[str, str] = {}


@contextmanager
def service_agent(stack: Any, journeys: list[Json]) -> Iterator[None]:
    if not any(j["shape"] == "service" for j in journeys):
        yield
        return
    loop = (stack.api.ok("GET", f"/api/v1/agents/{stack.seed['agentLoop']}") or {}).get("agent") or {}
    made = stack.api.ok("POST", "/api/v1/agents/create", {
        "name": f"Journeys service agent {secrets.token_hex(3)}", "description": "Runs as the org's service account.",
        "systemPrompt": "You are a workplace productivity assistant.", "shareWithOrg": True, "isServiceAccount": True})
    key = str(((made or {}).get("agent") or made or {}).get("_key") or "")
    try:
        stack.api.ok("PUT", f"/api/v1/agents/{key}", {
            "knowledge": [{"connectorId": k.get("connectorId")} for k in loop.get("knowledge") or [] if k.get("connectorId")],
            "mcpServers": [{"instanceId": m.get("instanceId"), "name": m.get("name"),
                            "tools": [{"name": t.get("name"), "fullName": t.get("fullName")} for t in m.get("tools") or []]}
                           for m in loop.get("mcpServers") or []],
            "skills": [{"name": k.get("name")} for k in loop.get("skills") or []],
            "toolsets": [{k: t.get(k) for k in ("name", "displayName", "type", "instanceId", "instanceName")}
                         | {"tools": [{"name": x.get("name"), "fullName": x.get("fullName")} for x in t.get("tools") or []]}
                         for t in loop.get("toolsets") or []],
            "webSearch": {"provider": WEB_PROVIDER}})
        # A service account acts with its own sign-in to each toolset, which an admin makes for it on the agent
        # (R33JGAPS-FU-5): the Jira toolset's here, as the people's own are made in jira_toolset.
        for t in loop.get("toolsets") or []:
            if t.get("instanceName") == JIRA_NAME and t.get("instanceId"):
                stack.api.ok("POST", f"{AGENT_TOOLSETS}/{key}/instances/{t['instanceId']}/authenticate",
                             {"auth": {"apiToken": stack.env["E2E_JIRA_TOKEN"]}})
        SERVICE_AGENT["key"] = key
        yield
    finally:
        SERVICE_AGENT.clear()
        if key:
            stack.api.request("DELETE", f"/api/v1/agents/{key}")


# --- the run ---------------------------------------------------------------------------------------------------------


@dataclass
class Result:
    journey: str
    path: str
    shape: str
    turn: str
    feature: str
    ok: bool
    detail: str
    seconds: float
    known: str = ""


def known_red(registry: Json) -> dict[tuple[str, str], str]:
    """(journey, feature) -> the follow-up a known red line is waiting on (registry ``knownRed``)."""
    return {(j, f): k["id"] for k in registry.get("knownRed", []) for j in k["journeys"] for f in k["features"]}


def consistent() -> list[str]:
    turned = {f for feats in TURNS.values() for f in feats}
    out = [f"SUPPORT {f} is in no turn" for f in SUPPORT if f not in turned]
    out += [f"TURNS {t} has no probe" for t in TURNS if t not in PROBES]
    out += [f"probe {t} is in no turn" for t in PROBES if t not in TURNS]
    return out


JOURNEY_DEADLINE_S = 420


def run_journey(stack: Any, shape: Json, journey: Json) -> list[Result]:
    ctx = Ctx(stack, shape, journey["path"], journey["shape"], secrets.token_hex(3),
              {"turn": journey["turn"], "journey": journey["id"]})
    started_at = time.monotonic()
    box: dict[str, list[Line]] = {}

    def probe() -> None:
        try:
            if ctx.path == "command":
                box["lines"] = command_turn(ctx, list(journey["features"]), journey["turn"])
            else:
                box["lines"] = PROBES[journey["turn"]](ctx, list(journey["features"]))
        except Exception as error:  # noqa: BLE001 - a journey's failure is a line, the run goes on
            box["lines"] = [(f, False, f"{type(error).__name__}: {str(error)[:400]}") for f in journey["features"]]

    # A run that waits on the person for something the probe does not answer keeps its stream open with keep-alives,
    # so no read times out: the journey has a deadline of its own, and the tier goes on without it.
    worker = threading.Thread(target=probe, daemon=True, name=journey["id"])
    worker.start()
    worker.join(JOURNEY_DEADLINE_S)
    late = (f"the journey did not end within {JOURNEY_DEADLINE_S} s (a stream still open: the run waits on something "
            "the probe does not answer)")
    lines = box.get("lines") or [(f, False, late) for f in journey["features"]]
    if "resumed" in ctx.notes:
        # A resume journey proves its feature after the pause: the run stopped for the person and went on.
        resumed = bool(ctx.notes["resumed"])
        lines = [(f, ok and resumed, f"{d}; resumed after the pause ({journey['path']}): {resumed}") for f, ok, d in lines]
    if "started" in ctx.notes:
        lines = [(f, ok, f"{d}; started by {ctx.notes['started']}") for f, ok, d in lines]
    if "slackAnswer" in ctx.notes:
        # A Slack journey proves its feature only when the thread got the answer (R34FIX-FU-2, ADR-OWD-09 D-SLACK-ASK);
        # a card turn also only when the bot handed the run to PipesHub instead of holding the thread.
        answered, said = ctx.notes["slackAnswer"]
        handed = ctx.notes.get("slackHandOff") or journey["turn"] not in ("deny", "allow")
        lines = [(f, ok and answered and bool(handed), f"{d}; {said}") for f, ok, d in lines]
    took = time.monotonic() - started_at
    out = [Result(journey["id"], journey["path"], journey["shape"], journey["turn"], f, ok, d, round(took, 1))
           for f, ok, d in lines]
    for r in out:
        print(f"  {'ok  ' if r.ok else 'FAIL'} {r.journey} {r.feature}: {r.detail} [{r.seconds}s]", flush=True)
    return out


def groups(journeys: list[Json]) -> list[tuple[tuple[str | None, str], list[Json]]]:
    """Journeys by the engine setting they need: the org's and the person's settings are shared, so journeys that need
    different ones never run at once. Those that need none run first, under the stack's own settings."""
    by: dict[tuple[str | None, str], list[Json]] = {}
    for j in journeys:
        by.setdefault(setting_of(j["path"], j["shape"]), []).append(j)
    return sorted(by.items(), key=lambda kv: (kv[0] != (None, "*"), kv[0][0] or "", kv[0][1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--tier", choices=("gate", "full"), default="gate")
    parser.add_argument("--only", default="", help="comma-separated journey ids (or path prefixes)")
    parser.add_argument("--parallel", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if problems := consistent():
        print("journeys: the runner's literals and its probes disagree: " + "; ".join(problems), file=sys.stderr)
        return 2
    run_dir = args.run_dir.resolve()
    # A stopped run still puts back what it set for the org and the people (the context managers' finally blocks).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    _import(run_dir)
    plan = json.loads(PLAN.read_text())
    registry = json.loads((ROOT / "docs/test-plan/data/journeys.json").read_text())
    tier = registry["tiers"][args.tier]
    by_id = {j["id"]: j for j in plan["journeys"]}
    chosen = [by_id[i] for i in plan["tiers"][args.tier]]
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        chosen = [j for j in chosen if any(j["id"] == w or j["id"].startswith(w) for w in wanted)]
    shape = json.loads(SHAPE.read_text())
    stack = Stack.attach(run_dir.name, "ST-1")
    # ADR-CAP-01 D4: drive the settled deployment, not the race with Node's first probe.
    sh.wait_capabilities_settled(stack)
    # R31CAPFIX-FU-1: every `knowledge` line reads the seeded travel policy; before it answers a search they are red
    # for a reason that is not retrieval, so the gate does not start.
    if (knowledge := sh.wait_knowledge_searchable(stack)) != "searchable":
        print(f"journeys: the seeded knowledge is not searchable: {knowledge}", file=sys.stderr)
        return 2
    apply_owner_shape(stack, shape)
    member = json.loads((run_dir / "member.json").read_text())
    PEOPLE["member"] = sh.Api(stack.api.base, member["email"], member["password"])
    PEOPLE_IDS["member"] = str(member.get("id") or "")
    parallel = args.parallel or tier.get("parallel", 4)
    began = time.monotonic()
    results: list[Result] = []
    print(f"journeys: {args.tier} tier, {len(chosen)} journeys, {parallel} at a time", flush=True)
    with (scripted_web_search(stack), scheduled_runs(stack), mcp_tokens(stack), jira_toolset(stack),
          watched_words(stack, chosen), service_agent(stack, chosen),
          slack_workspace(stack, chosen)):
        results += run_groups(stack, shape, chosen, parallel)
    took = round(time.monotonic() - began)
    return finish(args, registry, chosen, results, took, run_dir)


def run_groups(stack: Any, shape: Json, chosen: list[Json], parallel: int) -> list[Result]:
    results: list[Result] = []
    for (org, choice), members in groups(chosen):
        print(f"group org default {org or 'kept'}, person's choice {choice}: {len(members)} journeys", flush=True)
        # Only agent.deep's sandbox journey (MR-7) pauses the broker; the others run code on a settled stack.
        alone = [j for j in members if (j["turn"] in ALONE and (j["turn"] != "sandbox" or j["path"] == "agent.deep"))
                 or j["path"] in ALONE_PATHS or (j["turn"], j["path"], j["shape"]) in ALONE_CELLS]
        together = [j for j in members if j not in alone]
        try:
            with engine_setting(stack, org, choice):
                with ThreadPoolExecutor(max_workers=parallel) as pool:
                    for out in pool.map(lambda j: run_journey(stack, shape, j), together):
                        results.extend(out)
                for j in alone:
                    results.extend(run_journey(stack, shape, j))
        except Exception as error:  # noqa: BLE001 - a setting the stack refuses fails its group's journeys, not the run
            done = {r.journey for r in results}
            for j in members:
                if j["id"] in done:
                    continue
                for f in j["features"]:
                    results.append(Result(j["id"], j["path"], j["shape"], j["turn"], f, False,
                                          f"the group's setting (org {org}, choice {choice}) failed: "
                                          f"{type(error).__name__}: {str(error)[:300]}", 0.0))
                    print(f"  FAIL {j['id']} {f}: setting failed: {str(error)[:200]}", flush=True)
    return results


def finish(args: Any, registry: Json, chosen: list[Json], results: list[Result], took: int, run_dir: Path) -> int:
    known = known_red(registry)
    for r in results:
        finding = known.get((r.journey, r.feature))
        if finding is None:
            continue
        if r.ok:
            r.ok, r.detail = False, f"known red {finding} passes now: drop its knownRed entry with the fix. {r.detail}"
        else:
            r.known = finding
    failed = [r for r in results if not r.ok and not r.known]
    report = {"tier": args.tier, "seconds": took, "journeys": len(chosen), "lines": len(results),
              "failed": len(failed), "knownRed": sum(1 for r in results if r.known),
              "results": [asdict(r) for r in results]}
    out = args.out or run_dir / f"journeys-{args.tier}.json"
    out.write_text(json.dumps(report, indent=1))
    knowns = sorted({(r.journey, r.feature, r.known) for r in results if r.known})
    if knowns:
        print(f"known red ({len(knowns)}): {knowns}", flush=True)
    print(f"{len(results) - len(failed) - len(knowns)} of {len(results)} journey lines passed in {took} s"
          + (f"; failed: {sorted({(r.journey, r.feature) for r in failed})}" if failed else ""), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
