"""Redact sandbox filesystem paths from tool output before it reaches the LLM.

The coding/database sandboxes run user code inside either a local subprocess
tree (under ``/tmp/pipeshub_sandbox/<id>/``) or a Docker container
(``/output`` inside the container, backed by ``/tmp/pipeshub_sandbox_docker/
<id>/`` on the host). When user code prints such a path -- e.g.
``print(f"Saved at: {os.path.join(os.environ['OUTPUT_DIR'], name)}")`` --
the host path ends up in ``stdout`` and, without redaction, is handed
verbatim to the LLM, which then echoes it in the user-facing answer.

These helpers replace every known sandbox path with a stable placeholder
(``<output>`` / ``<workdir>``) so the model still sees *what* was produced
but not *where* on the host it lives. Filenames are preserved.
"""

from __future__ import annotations

import re
from typing import Final

# /tmp/pipeshub_sandbox/<id>/output[/...]  — also matches the macOS realpath
# form (/private/tmp/...). Must run before the workdir pattern below so the
# ``/output`` suffix collapses to a single placeholder.
_HOST_OUTPUT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:/private)?/tmp/pipeshub_sandbox(?:_docker)?/[^/\s]+/output"
    r"(?=[/\s)\],.;:]|$)"
)

# /tmp/pipeshub_sandbox/<id>[/...]  (anything under the sandbox root that
# wasn't captured as the output dir).
_HOST_WORKDIR_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:/private)?/tmp/pipeshub_sandbox(?:_docker)?/[^/\s]+"
)

# Bare ``/output`` — the Docker executor maps OUTPUT_DIR to ``/output``
# inside the container, so user prints inside Docker look like
# ``/output/file.pdf``. Require a non-alphanumeric char before the slash to
# avoid clobbering unrelated paths like ``/foo/output``.
_DOCKER_OUTPUT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])/output(?=[/\s)\],.;:]|$)"
)


def redact_sandbox_paths(text: str | None) -> str:
    """Replace sandbox host/container paths with stable placeholders.

    Safe to call on ``None`` or empty input -- returns ``""`` in that case.
    The substitutions are idempotent: calling the function twice yields the
    same result as calling it once.
    """
    if not text:
        return text or ""
    text = _HOST_OUTPUT_RE.sub("<output>", text)
    text = _HOST_WORKDIR_RE.sub("<workdir>", text)
    text = _DOCKER_OUTPUT_RE.sub("<output>", text)
    return text


__all__ = ["redact_sandbox_paths"]
