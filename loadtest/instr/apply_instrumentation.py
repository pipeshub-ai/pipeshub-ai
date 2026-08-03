#!/usr/bin/env python3
"""Apply per-query phase-timing instrumentation to chatbot.py and bridge.py.

Anchor-text only -- never line numbers, because both files get refactored out
from under any offset we might record. Every edit is scoped to a named function
first, since several anchors (notably the BlobStorage construction) appear in
more than one generator in the same file.

Validation is compile(), not ast.parse(): ast.parse accepts a `from __future__`
import placed after other statements, which then fails at import time.

Idempotent: re-running on an already-patched file is a no-op.

Usage:  apply_instrumentation.py <chatbot.py> <bridge.py>
"""

import sys

MARKER = "_phase_mark"

SHIM = '''try:
    from app.utils.query_phase_timing import phase_mark as _phase_mark, phase_start as _phase_start
except Exception:  # instrumentation module absent -> no-op, never break the service
    def _phase_start(logger): return None
    def _phase_mark(name): return None
'''


class PatchError(RuntimeError):
    pass


def _slice(src: str, path: str, start: str, end: str) -> "tuple[int, int]":
    i = src.find(start)
    if i < 0:
        raise PatchError(f"{path}: function-slice start anchor not found: {start!r}")
    if src.find(start, i + 1) >= 0:
        raise PatchError(f"{path}: function-slice start anchor is ambiguous: {start!r}")
    j = src.find(end, i)
    if j < 0:
        raise PatchError(f"{path}: function-slice end anchor not found: {end!r}")
    return i, j


def _sub(src: str, path: str, region: "tuple[int, int]", find: str, repl: str) -> str:
    lo, hi = region
    body = src[lo:hi]
    n = body.count(find)
    if n != 1:
        raise PatchError(
            f"{path}: anchor must appear exactly once in the target function, "
            f"found {n}:\n---\n{find}\n---"
        )
    return src[:lo] + body.replace(find, repl, 1) + src[hi:]


def _insert_shim(src: str, path: str, after: str) -> str:
    """Insert the import shim after `after`, enforcing __future__ placement."""
    n = src.count(after)
    if n != 1:
        raise PatchError(f"{path}: shim anchor must appear exactly once, found {n}: {after!r}")
    at = src.index(after) + len(after)
    fut = src.find("from __future__ import")
    if fut >= 0 and at <= fut:
        raise PatchError(
            f"{path}: shim would land before `from __future__ import` -- "
            "module would fail to import"
        )
    return src[:at] + SHIM + src[at:]


def patch_chatbot(src: str, path: str) -> str:
    src = _insert_shim(
        src,
        path,
        "from app.agents.chat_modes import resolve_chat_mode_policy, run_chat_stream\n",
    )
    region = _slice(
        src,
        path,
        "async def _generate_chat_stream_via_agent_loop(",
        '@router.post("/chat/stream"',
    )
    src = _sub(
        src, path, region,
        "    logger_ = container.logger()\n",
        "    logger_ = container.logger()\n    _phase_start(logger_)\n",
    )
    region = _slice(
        src, path,
        "async def _generate_chat_stream_via_agent_loop(",
        '@router.post("/chat/stream"',
    )
    src = _sub(
        src, path, region,
        "    policy = resolve_chat_mode_policy(query_info.chatMode)\n",
        '    _phase_mark("llm_config")\n    policy = resolve_chat_mode_policy(query_info.chatMode)\n',
    )
    region = _slice(
        src, path,
        "async def _generate_chat_stream_via_agent_loop(",
        '@router.post("/chat/stream"',
    )
    src = _sub(
        src, path, region,
        '    client_name = request.headers.get("client-name")\n',
        '    _phase_mark("user_context")\n    client_name = request.headers.get("client-name")\n',
    )
    return src


BRIDGE_EDITS = [
    (
        "        blob_store = BlobStorage(logger=log, config_service=config_service, graph_provider=graph_provider)\n",
        '        _phase_mark("connector_probe")\n'
        "        blob_store = BlobStorage(logger=log, config_service=config_service, graph_provider=graph_provider)\n",
    ),
    (
        '        filters = dict(query_info.get("filters") or {})\n',
        '        _phase_mark("attachments")\n'
        '        filters = dict(query_info.get("filters") or {})\n',
    ),
    (
        '        chat_state["citation_ref_mapper"] = ref_mapper\n',
        '        chat_state["citation_ref_mapper"] = ref_mapper\n'
        '        _phase_mark("chat_state")\n',
    ),
    (
        "                log=log,\n            )\n    except Exception as exc:\n",
        "                log=log,\n            )\n"
        '        _phase_mark("connector_prefetch")\n'
        "    except Exception as exc:\n",
    ),
    (
        "            prefetch_result = await prefetch_task if prefetch_task is not None else None\n",
        '            _phase_mark("agent_create")\n'
        "            prefetch_result = await prefetch_task if prefetch_task is not None else None\n"
        '            _phase_mark("retrieval_wait")\n',
    ),
    (
        "                result = agent.last_stream_result\n",
        "                result = agent.last_stream_result\n"
        '                _phase_mark("llm_stream")\n',
    ),
    (
        "                    streamed_answer=streamer.streamed_answer, reasoning_turns=streamer.reasoning_turns,\n                )\n",
        "                    streamed_answer=streamer.streamed_answer, reasoning_turns=streamer.reasoning_turns,\n                )\n"
        '                _phase_mark("finalize")\n',
    ),
]


def patch_bridge(src: str, path: str) -> str:
    src = _insert_shim(src, path, "from __future__ import annotations\n")
    for find, repl in BRIDGE_EDITS:
        region = _slice(src, path, "async def run_chat_stream(", "__all__ = ")
        src = _sub(src, path, region, find, repl)
    return src


def main(argv: "list[str]") -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    targets = [(argv[1], patch_chatbot), (argv[2], patch_bridge)]

    results = []
    for path, fn in targets:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        if MARKER in src:
            print(f"SKIP  {path} (already instrumented)")
            continue
        try:
            out = fn(src, path)
        except PatchError as exc:
            print(f"FAIL  {exc}", file=sys.stderr)
            return 1
        try:
            compile(out, path, "exec")
        except SyntaxError as exc:
            print(f"FAIL  {path}: patched source does not compile: {exc}", file=sys.stderr)
            return 1
        marks = out.count(f'{MARKER}("')
        results.append((path, out, marks))

    for path, out, marks in results:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(out)
        print(f"OK    {path} ({marks} phase marks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
