from __future__ import annotations

"""
agent-loop dev CLI — interactive REPL or single-shot query.

Config priority (highest → lowest):
  1. CLI flags          --api-key, --model, --role
  2. Environment vars   ANTHROPIC_API_KEY, AGENT_LOOP_MODEL, AGENT_LOOP_ROLE
  3. .env file          project root .env  (loaded automatically)

Usage:
  python -m agent_loop                          # interactive REPL
  python -m agent_loop "What is 2 + 2?"        # single query, then exit
  python -m agent_loop --query "What is 2+2?"  # same, explicit flag
  python -m agent_loop --model claude-opus-4-8 --verbose
  python -m agent_loop --role researcher "Explain asyncio"
"""
import argparse
import asyncio
import logging
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

# Keep Python's logging module silent — the CLI uses its own event-based output.
logging.disable(logging.INFO)
# logging.basicConfig(
#     level=logging.DEBUG,
#     format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
#     datefmt="%H:%M:%S",
#     stream=sys.stderr,
# )

from app.agent_loop_lib.core.responses import RunUsage
from app.agent_loop_lib.events.base import AgentEvent, EventEmitter, EventType

# ── Token usage & cost display ─────────────────────────────────────────────

# Approximate pricing per million tokens: (input $/MTok, output $/MTok)
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6":         (3.0,  15.0),
    "claude-opus-4-8":           (15.0, 75.0),
    "claude-haiku-4-5":          (0.80,  4.0),
    "claude-haiku-4-5-20251001": (0.80,  4.0),
}


def _fmt_usage(usage: "RunUsage | None", model: str) -> str:
    """Return a one-line usage summary from `Agent.usage`'s cumulative
    counters — the `RunUsage` accumulator `Agent` maintains itself,
    replacing the old pattern of reading counters off a concrete
    transport instance."""
    if usage is None:
        return ""
    in_tok      = usage.input_tokens
    out_tok     = usage.output_tokens
    calls       = usage.requests
    cache_read  = usage.cache_read_tokens
    cache_write = usage.cache_write_tokens
    if not (in_tok or out_tok):
        return ""

    in_price, out_price = _MODEL_PRICES.get(model, (3.0, 15.0))
    # Cache reads cost 10% of input price; cache writes cost 125%
    cache_read_price  = in_price * 0.10
    cache_write_price = in_price * 1.25
    cost = (
        in_tok      * in_price
        + out_tok   * out_price
        + cache_read  * cache_read_price
        + cache_write * cache_write_price
    ) / 1_000_000

    def _k(n: int) -> str:
        return f"{n/1000:.1f}k" if n >= 1000 else str(n)

    parts = [
        DIM(f"  ↳ {calls} LLM call{'s' if calls != 1 else ''} · "),
        DIM(f"{_k(in_tok)} in / {_k(out_tok)} out"),
    ]
    if cache_read or cache_write:
        parts.append(DIM(f" · cache ✓{_k(cache_read)} ↑{_k(cache_write)}"))
    parts.append(DIM(f" · ~${cost:.4f}"))
    return "".join(parts)


# ── .env loader (no external deps) ────────────────────────────────────────

def _load_dotenv(path: Path) -> None:
    """Parse a .env file and set vars into os.environ (only if not already set)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip optional surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _find_dotenv() -> Path | None:
    """Walk upward from cwd looking for a .env file."""
    here = Path.cwd()
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
        # Stop at filesystem root or a known project boundary
        if (directory / "pyproject.toml").exists() or (directory / "setup.py").exists():
            return candidate if candidate.exists() else None
    return None


# ── ANSI colour helpers (no external dep) ──────────────────────────────────

def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"

DIM     = lambda t: _c("2",  t)
BOLD    = lambda t: _c("1",  t)
CYAN    = lambda t: _c("36", t)
GREEN   = lambda t: _c("32", t)
YELLOW  = lambda t: _c("33", t)
RED     = lambda t: _c("31", t)
BLUE    = lambda t: _c("34", t)


# ── Event icons ────────────────────────────────────────────────────────────

_ICONS: dict[str, str] = {
    "agent_start":         "🚀",
    "agent_complete":      "✅",
    "agent_cancelled":     "⛔",
    "agent_failed":        "❌",
    "llm_call":            "🧠",
    "tool_call":           "🔧",
    "task_complete":       "🏁",
    "spawn_agent":         "🤖",
    "hil_pause":           "⏸ ",
    "plan_critique":       "🔍",
    "result_critique":     "🔎",
    "supervisor_approved": "✔ ",
    "supervisor_blocked":  "🚫",
    "budget_exceeded":     "💸",
}


class CLIEventEmitter(EventEmitter):
    """Prints agent events to stdout in real time."""

    def __init__(self, verbose: bool = False) -> None:
        self._verbose = verbose

    async def emit(self, event: AgentEvent) -> None:
        etype = event.event_type
        payload = event.payload

        if etype == EventType.AGENT_START:
            goal = payload.get("goal", "")
            print(CYAN(f"\n  ▶ {goal[:100]}"))

        elif etype == EventType.TOOL_CALL and self._verbose:
            tool = payload.get("tool", "")
            args = payload.get("args", {})
            arg_str = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
            print(DIM(f"    ⚙  {tool}({arg_str})"))

        elif etype == EventType.TOOL_RESULT and self._verbose:
            tool = payload.get("tool", "")
            is_err = payload.get("is_error", False)
            content = payload.get("content", "")[:60]
            marker = RED("✗") if is_err else DIM("→")
            print(DIM(f"    {marker} {tool}: {content}"))

        elif etype == EventType.TOOL_BLOCKED:
            tool = payload.get("tool", "")
            reason = payload.get("reason", "")
            print(YELLOW(f"    ⛔ {tool} blocked: {reason}"))

        elif etype == EventType.CANCELLATION:
            print(YELLOW("  ⛔ Cancelled"))

        elif etype == EventType.TOOL_CALL and payload.get("tool") == "clarify":
            # StdinHILStore prints the question itself — just leave a blank line
            print()


# ── Execution trace renderer ───────────────────────────────────────────────

def _event_label(entry: Any) -> str:
    """One-line human label for a single timeline entry."""
    et     = entry.event_type
    detail = entry.detail or {}

    if et == "agent_start":
        goal = detail.get("goal", entry.summary)[:72]
        return CYAN(f'🚀 "{goal}"')

    if et == "llm_call":
        n = detail.get("turn_index", "?")
        return DIM(f"🧠  LLM turn {n}")

    if et == "tool_call":
        tool = detail.get("tool", "")
        args = detail.get("args", {})
        if tool == "web_search":
            q = args.get("query", "")[:55]
            return f'🔍 web_search({DIM(repr(q))})'
        if tool == "web_scrape":
            url = args.get("url", "")[:60]
            return f'🌐 web_scrape({DIM(url)})'
        if tool == "spawn_agent":
            role = args.get("role", "?")
            goal = args.get("goal", "")[:45]
            return BLUE(f'🤖 spawn_agent({role}) → {DIM(repr(goal))}')
        if tool == "task_complete":
            return GREEN("🏁 task_complete")
        if tool == "clarify":
            q = args.get("question", "")[:50]
            return YELLOW(f'❓ clarify({DIM(repr(q))})')
        return f"🔧 {tool}"

    if et == "spawn_agent":
        args = detail.get("args", {})
        role = args.get("role", "?")
        goal = args.get("goal", "")[:50]
        reasoning = args.get("reasoning", "")
        marker = "⚡ " if args.get("_parallel") else ""
        reason_hint = DIM(f"  ← {reasoning[:60]}") if reasoning else ""
        return BLUE(f'🤖 {marker}spawning [{role}]  {DIM(repr(goal))}') + reason_hint

    if et == "task_complete":
        out = detail.get("output", "")[:72]
        return GREEN(f'🏁 {repr(out)}')

    if et == "agent_complete":
        out = detail.get("output", "")[:72]
        return GREEN(f'✅ {repr(out) if out else "done"}')

    if et == "agent_failed":
        return RED(f"❌ {entry.summary}")

    if et == "hil_pause":
        q = detail.get("question", "")[:55]
        return YELLOW(f'⏸  "{q}"')

    if et == "budget_exceeded":
        return RED("💸 budget exceeded")

    if et == "agent_cancelled":
        return YELLOW("⛔ cancelled")

    if et == "task_routed":
        route = detail.get("route", "?")
        action = detail.get("action", "")
        colour = GREEN if route == "solo" else BLUE
        return colour(f"🗺  route={route}  {DIM(action)}")

    icon = _ICONS.get(et, "·")
    return f"{icon} {entry.summary[:65]}"


def render_tree(entries: list[Any]) -> str:
    """Render a full execution trace showing agent hierarchy, roles, goals, and results."""
    if not entries:
        return "  (no timeline entries)"

    # ── Build indexes ──────────────────────────────────────────────────────
    by_run:    dict[str, list[Any]] = {}
    parent_of: dict[str, str | None] = {}
    role_of:   dict[str, str] = {}
    model_of:  dict[str, str] = {}

    for e in entries:
        by_run.setdefault(e.run_id, []).append(e)
        parent_of[e.run_id] = e.parent_run_id
        if getattr(e, "role_name", ""):
            role_of[e.run_id] = e.role_name
        if getattr(e, "model", ""):
            model_of[e.run_id] = e.model

    for rid in by_run:
        by_run[rid].sort(key=lambda e: e.sequence_id)

    children_of: dict[str, list[str]] = {}
    for rid, pid in parent_of.items():
        if pid and pid in by_run:
            children_of.setdefault(pid, []).append(rid)

    roots = [
        rid for rid in by_run
        if not parent_of.get(rid) or parent_of[rid] not in by_run
    ]

    trace_id = entries[0].trace_id if entries else "?"
    lines: list[str] = [
        "",
        BOLD("  📋 Execution Trace") + DIM(f"  trace={trace_id[:8]}"),
        "",
    ]

    def render_run(run_id: str, prefix: str, body_prefix: str) -> None:
        run_entries = by_run.get(run_id, [])
        role   = role_of.get(run_id, "agent")
        model  = model_of.get(run_id, "")
        is_sub = bool(parent_of.get(run_id))

        # ── Agent header box ──────────────────────────────────────────────
        model_hint = DIM(f"  {model}") if model else ""
        run_hint   = DIM(f"  run={run_id[:8]}")
        if is_sub:
            lines.append(prefix + BLUE(f"╔══ [{role}]{model_hint}{run_hint}"))
        else:
            lines.append(prefix + BOLD(f"┌── [{role}]{model_hint}{run_hint}"))

        # Goal from agent_start entry
        for e in run_entries:
            if e.event_type == "agent_start":
                goal = e.detail.get("goal", "")[:80]
                if goal:
                    lines.append(body_prefix + DIM(f'│   goal: "{goal}"'))
                break
        lines.append(body_prefix + DIM("│"))

        # ── Match spawn_agent events to child run_ids (in order) ──────────
        pending_children = list(children_of.get(run_id, []))

        # Build interleaved list: entries + child runs inserted after their spawn event
        interleaved: list[tuple[str, Any]] = []
        for entry in run_entries:
            if entry.event_type == "agent_start":
                continue  # shown in header above
            interleaved.append(("entry", entry))
            if entry.event_type == "spawn_agent" and pending_children:
                child = pending_children.pop(0)
                interleaved.append(("child", child))

        # Any children not yet inserted (shouldn't happen in normal flow)
        for child in pending_children:
            interleaved.append(("child", child))

        # ── Render each item ──────────────────────────────────────────────
        for idx, (kind, item) in enumerate(interleaved):
            is_last = idx == len(interleaved) - 1
            arm  = "└─ " if is_last else "├─ "
            down = "   " if is_last else "│  "

            if kind == "entry":
                label  = _event_label(item)
                ts     = DIM(item.timestamp[11:19] if len(item.timestamp) >= 19 else "")
                st     = DIM(f"[{item.status.value}]")
                lines.append(f"{body_prefix}│  {arm}{label}  {st} {ts}")
            else:
                child_prefix = body_prefix + "│  " + arm
                child_body   = body_prefix + "│  " + down
                render_run(item, child_prefix, child_body)

        if is_sub:
            lines.append(body_prefix + BLUE("╚" + "═" * 48))
        else:
            lines.append(body_prefix + DIM("└" + "─" * 48))
        lines.append("")

    for root in roots:
        render_run(root, "  ", "  ")

    return "\n".join(lines)


# ── Streaming turn loop (Phase 4) — incremental CLI rendering ─────────────

async def _run_streaming(agent: Any, goal: Any) -> Any:
    """Drive `agent.stream(goal)` (see agent/streaming.py), printing assistant
    text deltas to stdout as they arrive instead of only the final result.
    Every event (including tool_call/tool_result) still reaches whatever
    `event_emitter` was already configured on the agent — CLIEventEmitter's
    existing verbose output keeps working unmodified; this only ADDS live
    text rendering on top.

    Returns the final AgentResult (`agent.last_stream_result` once the
    generator is exhausted — see Agent.stream()'s docstring for why an
    AsyncGenerator can't just return it directly).
    """
    from app.agent_loop_lib.events.base import EventType

    streamed_turns: list[str] = []
    current_chunks: list[str] = []
    in_message = False

    async for event in agent.stream(goal):
        if event.event_type == EventType.TEXT_MESSAGE_START:
            current_chunks = []
            in_message = True
            print()
        elif event.event_type == EventType.TEXT_MESSAGE_CONTENT:
            delta = event.payload.get("delta", "")
            current_chunks.append(delta)
            sys.stdout.write(delta)
            sys.stdout.flush()
        elif event.event_type == EventType.TEXT_MESSAGE_END:
            if in_message:
                streamed_turns.append("".join(current_chunks))
                print()
            in_message = False

    result = agent.last_stream_result
    if result is not None and result.success:
        final_output = str(result.output or "").strip()
        last_streamed = streamed_turns[-1].strip() if streamed_turns else ""
        # A plain text completion (no tool call) was already fully streamed
        # above — final_output IS that same text, so printing it again would
        # duplicate it. A task_complete-driven completion's output was never
        # streamed as text (it's a tool-call argument, not assistant text),
        # so it still needs printing here.
        if final_output and final_output != last_streamed:
            print()
            for line in final_output.split("\n"):
                print("  " + line)
            print()

    return result


# ── Coding sandbox (opt-in via env var) ────────────────────────────────────

def _coding_sandbox_config_from_env():
    """Build a `CodingSandboxConfig` from `AGENT_LOOP_CODING_SANDBOX` (unset
    by default — the CLI stays exactly as before unless a dev opts in).

    `AGENT_LOOP_CODING_SANDBOX=local` or `=e2b` enables the `run_code`/
    `install_packages`/`read_sandbox_file` tools for this CLI session.
    `backend="e2b"` picks up `E2B_API_KEY` from the environment automatically
    (both here and, redundantly but harmlessly, inside the E2B SDK itself —
    see `E2BBackendConfig.api_key`'s docstring) — no other wiring needed.
    Returns `None` when unset, so callers can skip passing `coding_sandbox`
    entirely and get `ControlPlaneConfig`'s default (disabled).
    """
    backend = os.environ.get("AGENT_LOOP_CODING_SANDBOX", "").strip().lower()
    if backend not in ("local", "e2b"):
        return None

    from app.agent_loop_lib.control_plane.config import (
        CodingSandboxConfig,
        E2BBackendConfig,
    )

    artifact_dir = os.environ.get("AGENT_LOOP_ARTIFACT_DIR", "").strip() or None

    if backend == "e2b":
        return CodingSandboxConfig(
            enabled=True, backend="e2b",
            e2b=E2BBackendConfig(api_key=os.environ.get("E2B_API_KEY")),
            artifact_output_dir=artifact_dir,
        )
    return CodingSandboxConfig(enabled=True, backend="local", artifact_output_dir=artifact_dir)


def _skill_manager_config_from_env():
    """Skills (search/load/self-learning) are ON by default for the CLI —
    a project-local `.agent-loop/skills/` library, auto-created on first
    run, is exactly the "everything via tool calls" self-learning loop the
    framework is built around, and it should be visible without any config.
    `AGENT_LOOP_SKILLS_DISABLE=1` turns the whole feature off; `AGENT_LOOP_
    SKILLS_DIR` points it elsewhere. `AGENT_LOOP_SKILLS_AUTO_APPROVE`
    defaults to "true" here (unlike the library's own default of requiring
    review) since the CLI doesn't expose a review UI yet for candidates
    queued under `skills_dir/_meta/candidates/` — set it to "false" to get
    that (safer, human-in-the-loop) behavior instead.
    """
    if os.environ.get("AGENT_LOOP_SKILLS_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return None

    from app.agent_loop_lib.control_plane.config import SkillManagerConfig

    skills_dir = os.environ.get("AGENT_LOOP_SKILLS_DIR", "").strip() or ".agent-loop/skills"
    auto_approve = os.environ.get("AGENT_LOOP_SKILLS_AUTO_APPROVE", "true").strip().lower() not in ("0", "false", "no")
    return SkillManagerConfig(skills_dir=skills_dir, auto_approve=auto_approve)


def _extra_tool_names_from_env() -> list[str] | None:
    """`AGENT_LOOP_EXTRA_TOOLS=run_code,install_packages,...` — a comma-
    separated list of extra tool names granted on top of the chosen role's
    own `allowed_tools`, for ad hoc CLI testing of tools no builtin role
    references yet (e.g. the coding-sandbox tools). Unset by default, so
    normal role-based tool scoping is untouched unless a dev opts in."""
    raw = os.environ.get("AGENT_LOOP_EXTRA_TOOLS", "").strip()
    if not raw:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


def _make_spec_kwargs_with_extra_tools(cp: Any, role: str, max_turns: int) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"max_turns": max_turns}
    extra = _extra_tool_names_from_env()
    if extra:
        base = list(cp.role_registry.resolve(role).allowed_tools)
        kwargs["tool_names"] = base + [t for t in extra if t not in base]
    return kwargs


# ── Single-query runner ────────────────────────────────────────────────────

async def run_query(
    query: str,
    api_key: str,
    model: str,
    role: str,
    show_tree: bool,
    verbose: bool,
    max_turns: int = 50,
    stream: bool = False,
) -> int:
    """Run a single query, print the result, return exit code."""
    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.control_plane.config import (
        ControlPlaneConfig,
        LazyToolsetsConfig,
    )
    from app.agent_loop_lib.control_plane.control_plane import ControlPlane
    from app.agent_loop_lib.core.types import Goal
    from app.agent_loop_lib.modules.stores.hil.stdin import StdinHILStore

    coding_sandbox_cfg = _coding_sandbox_config_from_env()
    skill_manager_cfg = _skill_manager_config_from_env()
    cfg = ControlPlaneConfig(
        api_key=api_key,
        model=model,
        enable_timeline=show_tree,
        enable_state_tracking=True,
        hooks=["logging", "skill_learning"],
        tools=["all"],
        lazy_toolsets=LazyToolsetsConfig(pinned_toolsets=["web_search"]),
        **({"coding_sandbox": coding_sandbox_cfg} if coding_sandbox_cfg else {}),
        **({"skill_manager": skill_manager_cfg} if skill_manager_cfg else {}),
    )
    emitter = CLIEventEmitter(verbose=verbose)

    async with ControlPlane(cfg) as cp:
        # event_emitter/hil_store live on the shared AgentRuntime, not the
        # AgentSpec — set them once here so this run's clarify() prompts
        # stdin instead of blocking forever.
        cp.runtime.event_emitter = emitter
        cp.runtime.hil_store = StdinHILStore()
        agent_spec = cp.make_spec(role, **_make_spec_kwargs_with_extra_tools(cp, role, max_turns))
        agent = Agent(agent_spec, cp.runtime)

        try:
            if stream:
                result = await _run_streaming(agent, Goal(description=query))
            else:
                result = await agent.run(Goal(description=query))
        except Exception as exc:
            print(RED(f"\n  ✗ {exc}"), file=sys.stderr)
            return 1

        if result.success:
            if not stream:
                output = str(result.output or "").strip()
                if output:
                    print()
                    for line in output.split("\n"):
                        print("  " + line)
                    print()
        else:
            print(RED(f"\n  ✗ {result.error}"), file=sys.stderr)

        usage_line = _fmt_usage(agent.usage, model)
        if usage_line:
            print(usage_line)
            print()

        if not result.success:
            return 1

        if show_tree and cp._timeline_store:
            entries = await cp._timeline_store.get_by_trace(agent.run_ctx.trace_id)
            print(render_tree(entries))

    return 0


# ── Interactive REPL ───────────────────────────────────────────────────────

HELP_TEXT = """\

  Commands
  ────────────────────────────────────────────
  /help            Show this help
  /quit  /exit     Exit
  /tree            Re-show last execution tree
  /role <name>     Switch role
  /roles           List available roles
  /skills          List loaded skills (name, category, description)
  /verbose         Toggle real-time tool output
  /stream          Toggle incremental text streaming
  /model <name>    Switch model mid-session
  /clear           Reset (informational only)
  /commands        List custom markdown commands
  /<name> [args]   Run a custom command (see .agent-loop/commands/*.md)
  Ctrl-C           Interrupt or exit
"""

# Custom commands (Phase 3, commands/loader.py): one flat directory of
# `<name>.md` files, project-local first so a repo can ship its own without
# touching the user's global set — same override order as .env discovery.
_COMMANDS_DIRS = [
    str(Path.home() / ".agent-loop" / "commands"),
    ".agent-loop/commands",
]


async def repl(
    api_key: str,
    model: str,
    role: str,
    show_tree: bool,
    verbose: bool,
    max_turns: int = 50,
    stream: bool = False,
) -> None:
    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.control_plane.config import (
        ControlPlaneConfig,
        LazyToolsetsConfig,
    )
    from app.agent_loop_lib.control_plane.control_plane import ControlPlane
    from app.agent_loop_lib.core.types import Goal
    from app.agent_loop_lib.modules.stores.hil.stdin import StdinHILStore

    current_model = model
    current_role  = role
    last_entries: list[Any] = []

    coding_sandbox_cfg = _coding_sandbox_config_from_env()
    skill_manager_cfg = _skill_manager_config_from_env()
    cfg = ControlPlaneConfig(
        api_key=api_key,
        model=current_model,
        enable_timeline=True,
        enable_state_tracking=True,
        hooks=["logging", "skill_learning"],
        tools=["all"],
        commands_dirs=_COMMANDS_DIRS,
        lazy_toolsets=LazyToolsetsConfig(pinned_toolsets=["web_search"]),
        **({"coding_sandbox": coding_sandbox_cfg} if coding_sandbox_cfg else {}),
        **({"skill_manager": skill_manager_cfg} if skill_manager_cfg else {}),
    )
    emitter = CLIEventEmitter(verbose=verbose)

    print(BOLD(GREEN("\n  agent-loop")))
    print(DIM(f"  model={current_model}  role={current_role}"))

    async with ControlPlane(cfg) as cp:
        timeline_store = cp._timeline_store
        if cp.skills is not None:
            n_skills = len(cp.skills.catalog_snapshot())
            print(DIM(f"  skills={skill_manager_cfg.skills_dir}  ({n_skills} loaded) · /skills to browse"))
        print(DIM("  /help for commands · Ctrl-C to exit\n"))

        while True:
            # ── Prompt ───────────────────────────────────────────────────
            try:
                if sys.stdin.isatty():
                    sys.stdout.write(BOLD(CYAN(f"[{current_role}]")) + " > ")
                    sys.stdout.flush()
                loop = asyncio.get_event_loop()
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except (EOFError, KeyboardInterrupt):
                print("\n" + DIM("  Bye."))
                break

            if not line:  # EOF
                print(DIM("  Bye."))
                break

            text = line.strip()
            if not text:
                continue

            # ── Slash commands ────────────────────────────────────────────
            if text.startswith("/"):
                parts = text.split(None, 1)
                cmd   = parts[0].lower()
                arg   = parts[1].strip() if len(parts) > 1 else ""
                custom_name = cmd[1:]

                if cp.commands and cp.commands.has(custom_name) and cmd not in (
                    "/quit", "/exit", "/help", "/tree", "/roles", "/role", "/skills",
                    "/model", "/verbose", "/stream", "/clear", "/commands",
                ):
                    # Custom markdown command (commands/registry.py):
                    # expand its body and run the result as an ordinary
                    # goal, same code path as free-text input below.
                    text = cp.commands.render(custom_name, arg)
                    print(DIM(f"  ▶ /{custom_name} → running expanded prompt"))
                    # deliberately no `continue` — falls through to the
                    # agent-run block at the bottom of the loop

                elif cmd in ("/quit", "/exit"):
                    print(DIM("  Bye."))
                    break

                elif cmd == "/help":
                    print(HELP_TEXT)

                elif cmd == "/tree":
                    if last_entries:
                        print(render_tree(last_entries))
                    else:
                        print(DIM("  No run yet."))

                elif cmd == "/roles":
                    names = cp.role_registry.names()
                    print(DIM("  " + "  ".join(names)))

                elif cmd == "/role":
                    if not arg:
                        print(DIM(f"  role: {current_role}"))
                    elif cp.role_registry.has(arg):
                        current_role = arg
                        print(GREEN(f"  role → {current_role}"))
                    else:
                        print(RED(f"  Unknown role {arg!r}. Try /roles"))

                elif cmd == "/skills":
                    if cp.skills is None:
                        print(DIM("  Skills disabled (AGENT_LOOP_SKILLS_DISABLE=1)."))
                    else:
                        catalog = cp.skills.catalog_snapshot()
                        if not catalog:
                            print(DIM("  No skills yet — they accumulate automatically as you use the CLI."))
                        else:
                            for m in sorted(catalog, key=lambda m: (m.category or "", m.name)):
                                location = f"[{m.category}/{m.subcategory}]" if m.subcategory else f"[{m.category}]" if m.category else ""
                                print(f"  {GREEN(m.name)} {DIM(location)}  {m.description}")

                elif cmd == "/model":
                    if not arg:
                        print(DIM(f"  model: {current_model}"))
                    else:
                        current_model = arg
                        print(GREEN(f"  model → {current_model}"))

                elif cmd == "/verbose":
                    verbose = not verbose
                    emitter._verbose = verbose
                    print(DIM(f"  verbose: {'on' if verbose else 'off'}"))

                elif cmd == "/stream":
                    stream = not stream
                    print(DIM(f"  stream: {'on' if stream else 'off'}"))

                elif cmd == "/clear":
                    last_entries = []
                    print(DIM("  Cleared."))

                elif cmd == "/commands":
                    overview = cp.commands.overview() if cp.commands else []
                    if not overview:
                        print(DIM("  No custom commands found in " + " or ".join(_COMMANDS_DIRS)))
                    else:
                        for c in overview:
                            desc = f" — {c['description']}" if c["description"] else ""
                            print(DIM(f"  /{c['name']}{desc}"))

                else:
                    print(RED(f"  Unknown command: {cmd}  (try /help)"))
                    continue

                if not (cp.commands and cp.commands.has(custom_name)):
                    continue

            # ── Run the agent ─────────────────────────────────────────────
            # StdinHILStore is fresh per-turn so clarify() always prompts cleanly;
            # event_emitter/hil_store live on the shared runtime.
            cp.runtime.event_emitter = emitter
            cp.runtime.hil_store = StdinHILStore()
            spec_kwargs = _make_spec_kwargs_with_extra_tools(cp, current_role, max_turns)
            spec_kwargs["model"] = current_model
            agent_spec = cp.make_spec(current_role, **spec_kwargs)
            agent = Agent(agent_spec, cp.runtime)

            try:
                if stream:
                    result = await _run_streaming(agent, Goal(description=text))
                else:
                    result = await agent.run(Goal(description=text))
            except KeyboardInterrupt:
                print(YELLOW("\n  ⛔ Interrupted"))
                continue
            except Exception as exc:
                print(RED(f"\n  ✗ {exc}"))
                continue

            # ── Print result ──────────────────────────────────────────────
            if result.success:
                if not stream:
                    output = str(result.output or "").strip()
                    if output:
                        print()
                        for out_line in output.split("\n"):
                            print("  " + out_line)
                        print()
            else:
                print(RED(f"\n  ✗ {result.error}\n"))

            usage_line = _fmt_usage(agent.usage, current_model)
            if usage_line:
                print(usage_line)
                print()

            # ── Execution tree ────────────────────────────────────────────
            if show_tree and timeline_store:
                entries = await timeline_store.get_by_trace(agent.run_ctx.trace_id)
                last_entries = entries
                print(render_tree(entries))
                print()


# ── SSE server (Phase 4, serve/app.py) ─────────────────────────────────────

def _serve_main(argv: list[str]) -> None:
    """`agent-loop serve` — run the SSE server + playground UI. Handled as
    a separate mini-parser (checked for in main() before the main argparse
    setup below) rather than argparse subcommands, so the existing
    positional-query / REPL default behavior needs zero changes."""
    parser = argparse.ArgumentParser(
        prog="agent-loop serve",
        description="Serve the agent loop over HTTP as Server-Sent Events, with a browser playground at /playground",
    )
    parser.add_argument("--host", default=os.environ.get("AGENT_LOOP_HOST", "127.0.0.1"))
    parser.add_argument("--port", "-p", type=int, default=int(os.environ.get("AGENT_LOOP_PORT", "8000")))
    parser.add_argument("--transport", default=os.environ.get("AGENT_LOOP_TRANSPORT", "anthropic"))
    parser.add_argument("--api-key", default=os.environ.get("ANTHROPIC_API_KEY"))
    parser.add_argument("--model", "-m", default=os.environ.get("AGENT_LOOP_MODEL", "claude-sonnet-4-6"))
    parser.add_argument("--base-url", default=os.environ.get("AGENT_LOOP_BASE_URL"))
    args = parser.parse_args(argv)

    if args.transport == "anthropic" and not args.api_key:
        print(RED("  ✗ No API key."), file=sys.stderr)
        print(
            "  Add ANTHROPIC_API_KEY=sk-ant-... to your .env file or set it as an env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        import uvicorn
    except ImportError:
        print(RED("  ✗ 'serve' extras not installed."), file=sys.stderr)
        print("  Install with: pip install 'agent-loop[serve]'", file=sys.stderr)
        sys.exit(1)

    from app.agent_loop_lib.control_plane.config import ControlPlaneConfig
    from app.agent_loop_lib.serve.app import create_app

    cfg = ControlPlaneConfig(
        transport=args.transport,
        api_key=args.api_key,
        model=args.model,
        base_url=args.base_url,
        enable_timeline=True,
        enable_state_tracking=True,
        tools=["all"],
        hooks=["logging"],
    )
    app = create_app(cfg)

    print(GREEN(f"\n  agent-loop serve  →  http://{args.host}:{args.port}"))
    print(DIM(f"  playground        →  http://{args.host}:{args.port}/playground"))
    print(DIM("  POST /runs        {\"goal\": \"...\", \"role\": \"assistant\"}  (SSE response)\n"))
    uvicorn.run(app, host=args.host, port=args.port)


# ── CLI entry point ────────────────────────────────────────────────────────

def main() -> None:
    # Load .env before argparse so env vars are available as defaults
    dotenv = _find_dotenv()
    if dotenv:
        _load_dotenv(dotenv)

    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        _serve_main(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(
        prog="agent-loop",
        description="agent-loop dev CLI — REPL or single-shot query",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Config (priority: flag > env var > .env file):
          ANTHROPIC_API_KEY    API key
          AGENT_LOOP_MODEL     Model override
          AGENT_LOOP_ROLE      Default role

        .env file is auto-loaded from the project root (walks up from cwd).

        Examples:
          python -m agent_loop
          python -m agent_loop "What Python files are here?"
          python -m agent_loop --query "Summarise this repo" --role researcher
          python -m agent_loop --model claude-opus-4-8 --verbose
          python -m agent_loop --no-tree "Quick question"
        """),
    )

    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Run a single query and exit (omit for interactive REPL)",
    )
    parser.add_argument(
        "--query", "-q",
        dest="query_flag",
        default=None,
        metavar="QUERY",
        help="Same as positional query argument",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ANTHROPIC_API_KEY"),
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--model", "-m",
        default=os.environ.get("AGENT_LOOP_MODEL", "claude-sonnet-4-6"),
        help="Model [default: claude-sonnet-4-6]",
    )
    parser.add_argument(
        "--role", "-r",
        default=os.environ.get("AGENT_LOOP_ROLE", "assistant"),
        help="Role [default: assistant]",
    )
    parser.add_argument(
        "--no-tree",
        action="store_true",
        help="Hide the execution tree",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show tool calls and results in real time",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Render assistant text incrementally as it streams from the model",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=50,
        metavar="N",
        help="Max LLM turns per query [default: 50]",
    )

    args = parser.parse_args()

    # --query flag overrides positional query
    query = args.query_flag or args.query

    if not args.api_key:
        print(RED("  ✗ No API key."), file=sys.stderr)
        print(
            "  Add ANTHROPIC_API_KEY=sk-ant-... to your .env file or set it as an env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    show_tree = not args.no_tree

    try:
        if query:
            # Single-shot mode
            code = asyncio.run(run_query(
                query=query,
                api_key=args.api_key,
                model=args.model,
                role=args.role,
                show_tree=show_tree,
                verbose=args.verbose,
                max_turns=args.max_turns,
                stream=args.stream,
            ))
            sys.exit(code)
        else:
            # Interactive REPL
            asyncio.run(repl(
                api_key=args.api_key,
                model=args.model,
                role=args.role,
                show_tree=show_tree,
                verbose=args.verbose,
                max_turns=args.max_turns,
                stream=args.stream,
            ))
    except KeyboardInterrupt:
        print()
