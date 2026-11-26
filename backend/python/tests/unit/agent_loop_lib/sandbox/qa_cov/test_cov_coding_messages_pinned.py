"""Every message the model or the user reads about code sandbox capacity,
stops and limits, pinned word for word, and every message or log line the
deployment docs quote, checked against the code that produces it.

A change to one of these texts is a product change: update the table and,
where the docs quote it, the docs in the same commit."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent_loop_lib.sandbox.capacity import HoldReason
from app.agent_loop_lib.sandbox.coding import docker as docker_mod
from app.agent_loop_lib.sandbox.coding.base import CodeResult, ErrorCategory
from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine
from app.agent_loop_lib.sandbox.governor import message as hold_message
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
    _at_cap_suggestion,
    sandbox_limit_guidance,
    stopped_output,
    unknown_sandbox_guidance,
)
from app.agents.agent_loop import sandbox_bridge
from app.services.sandbox_alerts import event as alert_event

_REPO = Path(__file__).resolve().parents[7]
_PY_APP = _REPO / "backend" / "python" / "app"
_ADVANCED = _REPO / "deployment" / "docker-compose" / "ADVANCED_DEPLOYMENT.md"


# --- what a waiting run is told when it gives up ------------------------------

@pytest.mark.parametrize(("reason", "kwargs", "text"), [
    (HoldReason.SLOTS_FULL, {"held": 1, "limit": 1},
     "the only code sandbox on this server is busy (1/1); retry once it is released"),
    (HoldReason.SLOTS_FULL, {"held": None, "limit": 1},
     "the only code sandbox on this server is busy; retry once it is released"),
    (HoldReason.SLOTS_FULL, {"held": 4, "limit": 4},
     "all 4 code sandboxes on this server are busy (4/4); retry once one is released"),
    (HoldReason.SLOTS_FULL, {"held": None, "limit": 4},
     "all 4 code sandboxes on this server are busy; retry once one is released"),
    (HoldReason.ORG_SHARE, {"held": 3, "limit": 8, "tag_held": 2, "org_limit": 2},
     "this organisation's share of code sandboxes is in use (2/2); retry once one of its runs ends"),
    (HoldReason.LIVE_MEMORY, {"held": 2, "limit": 6},
     "the server is short of memory right now (2/6 code sandboxes in use); retry in a minute"),
    (HoldReason.HOST_MEMORY_FLOOR, {"held": 2, "limit": 6},
     "the server is short of memory right now (2/6 code sandboxes in use); retry in a minute"),
    (HoldReason.LIVE_MEMORY, {"held": 2, "limit": 6, "memory_source": "sidecar"},
     "the code runner is short of memory right now (2/6 code sandboxes in use); retry in a minute"),
    (HoldReason.LIVE_CPU, {"held": 3, "limit": 6},
     "the server's CPU is busy, so fewer code sandboxes run at once (3/6); retry in a minute"),
    (HoldReason.LIVE_CPU, {"held": 3, "limit": 6, "memory_source": "sidecar"},
     "the code runner's CPU is busy, so fewer code sandboxes run at once (3/6); retry in a minute"),
])
def test_hold_messages(reason: HoldReason, kwargs: dict, text: str) -> None:
    assert hold_message(reason, **kwargs) == text


# --- tool results -------------------------------------------------------------------

_LIMIT_ERR = RuntimeError("all 4 code sandboxes on this server are busy (4/4)")

_TOOL_TEXTS = [
    ("stopped mid-run", stopped_output().error,
     "Stopped by the user before this run finished. The run was ended; files it was "
     "writing may be incomplete."),
    ("stopped before start", stopped_output(started=False).error,
     "Stopped by the user before this call started. Nothing ran."),
    ("refused, reuse helps", sandbox_limit_guidance(_LIMIT_ERR),
     "all 4 code sandboxes on this server are busy (4/4). This is a temporary capacity "
     "limit, not a problem with your code. Reuse an existing sandbox_id from earlier in "
     "this turn if you have one, or retry this call shortly."),
    ("refused per call", sandbox_limit_guidance(_LIMIT_ERR, reuse_helps=False),
     "all 4 code sandboxes on this server are busy (4/4). This is a temporary capacity "
     "limit, not a problem with your code. Every call needs a free slot while it runs, "
     "even on an existing sandbox, so retry this call shortly and run one call at a time."),
    ("unknown sandbox", unknown_sandbox_guidance(KeyError("x")),
     "'x'. Sandboxes exist only for the turn that created them, so a sandbox_id from an "
     "earlier turn or one inferred from a filename is never valid. Do not retry with "
     "another id: start a fresh sandbox by calling run_code with no sandbox_id, and "
     "re-stage any file you need as an input artifact rather than reading it out of a "
     "previous sandbox."),
    ("at the run time cap", _at_cap_suggestion(300.0),
     "The run hit the maximum run time of 300s. A larger timeout will not be accepted: "
     "make the code faster, process less data per run, or split the work across several "
     "runs (files written to the sandbox persist between runs with the same sandbox_id)."),
]


@pytest.mark.parametrize(("case", "actual", "expected"), _TOOL_TEXTS, ids=[c[0] for c in _TOOL_TEXTS])
def test_tool_texts(case: str, actual: str, expected: str) -> None:
    assert actual == expected


def test_stopped_result_fields() -> None:
    for started in (True, False):
        out = stopped_output(started=started)
        assert out.success is False
        assert out.end_fields == {"reason": "stopped", "error_class": "cancelled"}


# --- notes the Docker backend adds to a run's stderr -------------------------------

_DOCKER_NOTES = [
    ("oom", docker_mod._OOM_NOTE.format(limit=512),
     "[sandbox] The program was killed because it ran out of memory: either it went over "
     "this sandbox's 512 MB per run, or the server itself was short of memory. Use less "
     "memory (process data in chunks, smaller types) and try again.\n"),
    ("install timeout", docker_mod._INSTALL_TIMEOUT_NOTE.format(seconds=docker_mod._INSTALL_TIMEOUT_S),
     "[sandbox] Package installation timed out after 300s.\n"),
    ("install oom", docker_mod._INSTALL_OOM_NOTE.format(limit=512),
     "[sandbox] Package installation ran out of memory (limit 512 MB). Install fewer "
     "packages at once, or pick lighter ones.\n"),
    ("no network", docker_mod._NETWORK_UNAVAILABLE_NOTE,
     "[sandbox] Network access is unavailable for this run: the egress firewall could not "
     "be installed, so the code ran with no network.\n"),
    ("install firewall", docker_mod._INSTALL_FIREWALL_UNAVAILABLE.format(reason="image x"),
     "[sandbox] Package install refused: the egress firewall could not be installed for "
     "the install container (image x). Packages are never installed on an unfiltered "
     "network; use a sandbox image that includes iptables (deployment/sandbox/Dockerfile).\n"),
]


@pytest.mark.parametrize(("case", "actual", "expected"), _DOCKER_NOTES, ids=[c[0] for c in _DOCKER_NOTES])
def test_docker_notes(case: str, actual: str, expected: str) -> None:
    assert actual == expected


# --- reflection: what a failed run is diagnosed as ----------------------------------

_OOM_SUGGESTION = (
    "Reduce peak memory and run again: process the data in chunks or stream it instead "
    "of loading it all at once (for example pandas read_csv(chunksize=...)), use smaller "
    "dtypes, avoid keeping several copies of large data, and delete large objects once "
    "you are done with them. Running the same code again will fail the same way."
)
_LOCAL = ReflectionEngine(memory_limit_mb=512, cpu_limit_s=30, process_limit=64,
                          process_limit_shared=True, file_size_limit_mb=100)
_DOCKER = ReflectionEngine(memory_limit_mb=512, process_limit=256)
_UNKNOWN = ReflectionEngine()


def _run(exit_code: int, stderr: str = "", language: str = "python") -> CodeResult:
    return CodeResult(stdout="", stderr=stderr, exit_code=exit_code, language=language, duration_ms=1.0)


_REFLECTION = [
    ("oom certain, error", _DOCKER.out_of_memory("", certain=True, error="a MemoryError"),
     ErrorCategory.OUT_OF_MEMORY, False,
     "Out of memory: the run stopped with a MemoryError because it could not get more memory "
     "(its 512 MB limit, or the server was short of memory)."),
    ("oom certain", _DOCKER.out_of_memory("", certain=True), ErrorCategory.OUT_OF_MEMORY, False,
     "Out of memory: the run was killed for using more memory than allowed (its 512 MB limit, "
     "or the server was short of memory)."),
    ("oom certain, no limit known", _UNKNOWN.out_of_memory("", certain=True),
     ErrorCategory.OUT_OF_MEMORY, False,
     "The run was killed (SIGKILL) before it finished, most likely because it ran out of memory."),
    ("oom error, no limit known", _UNKNOWN.out_of_memory("", certain=True, error="a MemoryError"),
     ErrorCategory.OUT_OF_MEMORY, False,
     "Out of memory: the run stopped with a MemoryError because it could not get more memory."),
    ("sigkill", _DOCKER.analyze(_run(137)), ErrorCategory.OUT_OF_MEMORY, False,
     "The run was killed (SIGKILL) before it finished, most likely because it ran out of memory."),
    ("node heap", _DOCKER.analyze(_run(134, "FATAL ERROR: Reached heap limit Allocation failed - "
                                       "JavaScript heap out of memory", "typescript")),
     ErrorCategory.OUT_OF_MEMORY, False,
     "Out of memory: the run stopped with a JavaScript heap out-of-memory error because it could "
     "not get more memory (its 512 MB limit, or the server was short of memory)."),
    ("cpu limit", _LOCAL.analyze(_run(-24)), ErrorCategory.TIMEOUT, True,
     "[sandbox] The program used up its CPU time limit (30 s of CPU). Make it faster, or split "
     "the work into smaller runs."),
    ("cpu limit unknown", _UNKNOWN.cpu_time_exceeded(""), ErrorCategory.TIMEOUT, True,
     "[sandbox] The program used up its CPU time limit (CPU time). Make it faster, or split the "
     "work into smaller runs."),
    ("process limit, shared", _LOCAL.analyze(_run(1, "RuntimeError: can't start new thread")),
     ErrorCategory.RUNTIME, True,
     "[sandbox] The run hit its process limit (64 processes) and could not start another process "
     "or thread. The limit counts every process of the server's user, not only this run's."),
    ("process limit, container", _DOCKER.analyze(_run(1, "sh: fork: retry: Resource temporarily unavailable")),
     ErrorCategory.RUNTIME, True,
     "[sandbox] The run hit its process limit (256 processes) and could not start another "
     "process or thread."),
    ("process limit unknown", _UNKNOWN.process_limit_hit(""), ErrorCategory.RUNTIME, True,
     "[sandbox] The run hit its process limit and could not start another process or thread."),
    ("file size, signal", _LOCAL.analyze(_run(-25)), ErrorCategory.RUNTIME, True,
     "[sandbox] The run hit its file size limit (100 MB per file): a file it wrote grew too large."),
    ("file size, efbig", _DOCKER.analyze(_run(1, "OSError: [Errno 27] File too large")),
     ErrorCategory.RUNTIME, True,
     "[sandbox] The run hit its file size limit: a file it wrote grew too large."),
]


@pytest.mark.parametrize(("case", "analysis", "category", "retryable", "text"), _REFLECTION,
                         ids=[c[0] for c in _REFLECTION])
def test_reflection_messages(case: str, analysis, category: ErrorCategory, retryable: bool, text: str) -> None:
    assert analysis is not None
    assert analysis.category == category
    assert analysis.is_retryable is retryable
    assert analysis.message == text
    if category == ErrorCategory.OUT_OF_MEMORY:
        assert analysis.suggestion == _OOM_SUGGESTION


def test_limit_suggestions() -> None:
    assert _UNKNOWN.cpu_time_exceeded("").suggestion == (
        "Make the code faster, or split the work into smaller runs. A larger timeout does not "
        "help: the limit counts CPU time, not wall-clock time."
    )
    assert _UNKNOWN.process_limit_hit("").suggestion == (
        "Start fewer processes or threads (for example a smaller worker pool), or do the work "
        "in one process."
    )
    assert _UNKNOWN.file_size_limit_hit("").suggestion == (
        "Write smaller files: split the output into several files, compress it, or keep only "
        "the part you need."
    )


# --- what the deployment docs quote ---------------------------------------------------


def _doc() -> str:
    return _ADVANCED.read_text(encoding="utf-8")


def test_the_slot_wait_note_matches_the_docs() -> None:
    assert sandbox_bridge._SLOT_WAIT_MESSAGE == "waiting in queue"
    assert '"waiting in queue"' in _doc()


@pytest.mark.parametrize(("doc_text", "actual"), [
    ('**Warning** ("Code runs are waiting for capacity")', alert_event._TITLES["warning"]),
    ('**Critical** ("Code runs are being refused")', alert_event._TITLES["critical"]),
    ('in-app "Code runs are no longer held back" notice', alert_event.RESOLVED_TITLE),
])
def test_alert_titles_match_the_docs(doc_text: str, actual: str) -> None:
    assert doc_text in _doc()
    assert f'"{actual}"' in doc_text


def _source_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _PY_APP.rglob("*.py")
                     if "sandbox" in str(p) or "resource_governor" in str(p))


# Log lines the docs tell operators to look for; each must still be logged.
@pytest.mark.parametrize(("doc_quote", "source_pattern"), [
    ("one `sandbox capacity:` line", r'"sandbox capacity: mode=%s'),
    ("as `sandbox live limit`", r'"sandbox live limit %s->%d'),
    ("`asking indexing to slow down`", r'"asking indexing to slow down"'),
    ("`indexing back-off released`", r"indexing back-off released after"),
    ("`limits changed: … yield=on`", r'yield=%s source=%s\)",[\s\S]{0,400}"on" if yield_state\.active else "off"'),
    ("`sandbox load alert fired level=…`", r'"sandbox load alert fired level=%s'),
])
def test_log_lines_the_docs_quote_are_still_logged(doc_quote: str, source_pattern: str) -> None:
    assert doc_quote in _doc()
    assert re.search(source_pattern, _source_text()), source_pattern
