"""Query-profiling harness: bridge patch anchors and the turn-completion pattern."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "instr"))

import apply_instrumentation as ai  # noqa: E402

GATHER_END = "                demo_exclusions_for_run(graph_provider, config_service, user_info, log),\n            )\n        )\n"


def _bridge(*lines: str) -> str:
    return (
        "from __future__ import annotations\n\n"
        "async def run_chat_stream(query_info):\n"
        + "".join(lines)
        + "                result = agent.last_stream_result\n"
        "__all__ = []\n"
    )


def test_attachments_mark_skipped_when_attachments_resolve_inside_the_gather() -> None:
    src = _bridge(
        "        x = await asyncio.gather(\n            (\n",
        GATHER_END,
        '        filters = dict(query_info.get("filters") or {})\n',
    )
    out = ai.patch_bridge(src, "bridge.py")
    assert '_phase_mark("attachments")' not in out
    assert '_phase_mark("connector_prefetch")' in out


def test_attachments_mark_uses_filters_anchor_without_the_gather() -> None:
    out = ai.patch_bridge(_bridge('        filters = dict(query_info.get("filters") or {})\n'), "bridge.py")
    assert '_phase_mark("attachments")\n        filters = dict(' in out


def _turn_res() -> tuple[re.Pattern, re.Pattern]:
    sh = (ROOT / "_common.sh").read_text(encoding="utf-8")
    ok = re.search(r"^TURN_DONE_OK_RE='(.*)'$", sh, re.M).group(1)
    return re.compile(ok), re.compile(ok + "|AnswerFinalizer: agent run failed")


def test_turn_done_pattern_counts_each_turn_once() -> None:
    ok, done = _turn_res()
    log = [
        "AnswerFinalizer: answer empty after normalization, using fallback",
        "AnswerFinalizer: finalized response (120 chars, 0 citations)",
        "AnswerFinalizer: finalized STOPPED response (10 chars, 0 citations)",
        "AnswerFinalizer: empty response, using fallback",
        "AnswerFinalizer: agent run failed, answering with an error message (error_code=x): boom",
    ]
    assert sum(bool(done.search(line)) for line in log) == 4
    assert sum(bool(ok.search(line)) for line in log) == 3
