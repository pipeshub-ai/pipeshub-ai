"""Redact sandbox filesystem paths from tool output before it reaches the LLM.

The coding/database sandboxes run user code inside either a local subprocess
tree (under ``<tempdir>/pipeshub_sandbox-<uid>/<id>/``), a Docker container
(``/output`` inside the container, backed by ``<tempdir>/pipeshub_sandbox_docker-<uid>/
<id>/`` on the host), a broker sandbox (``<tempdir>/pipeshub_sandbox_broker-<uid>/<id>/``
on the worker), each under ``PIPESHUB_SANDBOX_ROOT`` instead when the operator
sets it (``app/sandbox/roots.py``, R13-ROOT), or -- on the agent-loop path -- ``agent_loop_lib``'s own
sandbox working directory (``/tmp/alcs-<id>/`` for the local backend,
``/tmp/alcs-docker-<id>/`` for the Docker backend; see
``app/agents/agent_loop/sandbox_bridge.py``). When user code prints such a
path -- e.g. ``print(f"Saved at: {os.path.join(os.environ['OUTPUT_DIR'], name)}")``
-- the host path ends up in ``stdout`` and, without redaction, is handed
verbatim to the LLM, which then echoes it in the user-facing answer.

These helpers replace every known sandbox path with a stable placeholder
(``<output>`` / ``<workdir>``) so the model still sees *what* was produced
but not *where* on the host it lives. Filenames are preserved.

User code can also print credentials it can read (this process's env through
``/proc``, a key in a file), so the same pass masks secrets with
``redact_secrets_in_text``.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Final

from app.sandbox.roots import NAMES, sandbox_root
from app.utils.log_redaction import redact_secrets_in_text

# /tmp/pipeshub_sandbox[_docker|_broker][-<uid>]/<id>/output[/...] — also matches
# the macOS realpath form (/private/tmp/...), and a process's own roots wherever
# they are (``_own_roots``). Must run before the workdir pattern below so the
# ``/output`` suffix collapses to a single placeholder.
_TMP_ROOT = r"(?:/private)?/tmp/pipeshub_sandbox(?:_docker|_broker)?(?:-\d+)?"
_HOST_OUTPUT_RE: Final[re.Pattern[str]] = re.compile(
    _TMP_ROOT + r"/[^/\s]+/output(?=[/\s)\],.;:]|$)"
)

# /tmp/pipeshub_sandbox.../<id>[/...]  (anything under a sandbox root that
# wasn't captured as the output dir).
_HOST_WORKDIR_RE: Final[re.Pattern[str]] = re.compile(_TMP_ROOT + r"/[^/\s]+")


@lru_cache(maxsize=8)
def _root_patterns(roots: tuple[str, ...]) -> tuple[re.Pattern[str], re.Pattern[str]]:
    alternatives = "|".join(re.escape(r) for r in sorted(set(roots), key=len, reverse=True))
    return (
        re.compile(rf"(?:{alternatives})/[^/\s]+/output(?=[/\s)\],.;:]|$)"),
        re.compile(rf"(?:{alternatives})/[^/\s]+"),
    )


def _own_roots() -> tuple[str, ...]:
    """This process's roots as written and resolved: a temp folder or an operator root outside ``/tmp``."""
    named = tuple(sandbox_root(n) for n in NAMES)
    return named + tuple(os.path.realpath(r) for r in named)

# agent_loop_lib coding-sandbox working dirs (agent-loop path only): local
# backend uses ``alcs-<id>``, Docker backend uses ``alcs-docker-<id>`` — see
# ``sandbox_bridge.py``'s ``working_dir_root``. Same output-then-workdir
# ordering rationale as the two patterns above.
_ALCS_OUTPUT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:/private)?/tmp/alcs(?:-docker)?-[^/\s]+/output"
    r"(?=[/\s)\],.;:]|$)"
)
_ALCS_WORKDIR_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:/private)?/tmp/alcs(?:-docker)?-[^/\s]+"
)

# Bare ``/output`` — the Docker executor maps OUTPUT_DIR to ``/output``
# inside the container, so user prints inside Docker look like
# ``/output/file.pdf``. Require a non-alphanumeric char before the slash to
# avoid clobbering unrelated paths like ``/foo/output``.
_DOCKER_OUTPUT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])/output(?=[/\s)\],.;:]|$)"
)


def redact_sandbox_paths(text: str | None) -> str:
    """Replace sandbox host/container paths with stable placeholders and
    mask secrets (``[REDACTED]``).

    Safe to call on ``None`` or empty input -- returns ``""`` in that case.
    The substitutions are idempotent: calling the function twice yields the
    same result as calling it once.
    """
    if not text:
        return text or ""
    own_output, own_workdir = _root_patterns(_own_roots())
    text = own_output.sub("<output>", text)
    text = _HOST_OUTPUT_RE.sub("<output>", text)
    text = own_workdir.sub("<workdir>", text)
    text = _HOST_WORKDIR_RE.sub("<workdir>", text)
    text = _ALCS_OUTPUT_RE.sub("<output>", text)
    text = _ALCS_WORKDIR_RE.sub("<workdir>", text)
    text = _DOCKER_OUTPUT_RE.sub("<output>", text)
    # shortcut: encoded or split secrets get through; the real fix for the local backend is confinement.
    return redact_secrets_in_text(text)


__all__ = ["redact_sandbox_paths"]
